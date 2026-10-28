#!/usr/bin/env python3
"""Opt-in real BLE test using synthetic HTTP events and an isolated local hub.

Nothing runs on import. This changes the bound watch's visible state when run.
Stop other watch bridge clients before running; this script does not stop them.
It does not read Codex/Claude/Cursor task hooks or claim to validate watch pixels.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import urllib.request

import bridge
from status_hub import HubServer, StatusHub


class CheckFailed(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailed(code)


def http_json(base_url, token, path, data=None):
    """Only the test's loopback server; do not use environment HTTP proxies."""
    body = None if data is None else json.dumps(data, allow_nan=False).encode()
    request = urllib.request.Request(
        base_url + path, data=body,
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        require(response.status == 200, 'hub_http_status')
        return json.loads(response.read(65536))


async def snapshot_from_http(base_url, token, snapshot_path):
    # GET publishes a fresh atomic snapshot. Consume that same file as bridge watch.
    served = await asyncio.to_thread(http_json, base_url, token, '/v1/snapshot')
    persisted = json.loads(snapshot_path.read_text())
    require(served == persisted, 'http_snapshot_file_mismatch')
    return persisted


async def exercise(args, base_url, token, snapshot_path, report):
    from bleak import BleakClient

    report['stage'] = 'load_existing_private_pairing'
    config = json.loads(args.config.read_text())
    report['stage'] = 'discover_bound_watch'
    device = await bridge.find_device(config)
    agent = None
    try:
        report['stage'] = 'pair_or_reuse_bound_watch'
        agent = await bridge.linux_agent(device)
        async with BleakClient(device, timeout=20, pair=sys.platform != 'linux') as client:
            report['stage'] = 'authenticate_watch'
            await bridge.read_info(client, config)
            relay = bridge.StatusRelay()
            event_seq = 0

            async def post(kind, *, phase=None, ttl=30):
                nonlocal event_seq
                event_seq += 1
                event = dict(version=1, provider='Codex', source='synthetic-hardware-qa',
                             session_id='isolated-qa', event_id=f'qa-{event_seq}',
                             seq=event_seq, kind=kind, timestamp=time.time(), ttl_seconds=ttl)
                if phase is not None:
                    event['phase'] = phase
                reply = await asyncio.to_thread(http_json, base_url, token, '/v1/events', event)
                require(reply.get('ok') is True, 'hub_event_not_accepted')
                return await snapshot_from_http(base_url, token, snapshot_path)

            report['states'] = []
            for state in bridge.STATES:
                report['stage'] = 'apply_' + state
                before = await bridge.read_info(client, config)
                snapshot = await post('task', phase=state)
                target = bridge.parse_source(snapshot)
                ack = await relay.step(client, config, target)
                require(ack is not None, 'state_write_missing')
                require(ack['accepted'] == ack['applied'] == before['accepted'] + 1,
                        'state_sequence_not_applied')
                require(ack['provider'] == 0 and ack['state'] == bridge.STATES.index(state),
                        'state_ack_mismatch')
                report['states'].append(dict(provider='Codex', state=state,
                                             accepted=ack['accepted'], applied=ack['applied'],
                                             firmware_ui_ack=True))
                print(f'Applied synthetic Codex / {state}', flush=True)

            report['stage'] = 'start_short_task_lease'
            snapshot = await post('task', phase='running', ttl=5)
            task_updated_at = snapshot['task_updated_at']
            task_expires_at = snapshot['task_expires_at']
            ack = await relay.step(client, config, bridge.parse_source(snapshot))
            require(ack is not None, 'short_lease_not_applied')
            baseline = {key: ack[key] for key in ('accepted', 'applied')}
            # Conservative clock starts after firmware UI acknowledgement, after write.
            last_applied_monotonic = time.monotonic()
            await asyncio.sleep(6)

            report['stage'] = 'source_heartbeat_after_task_expiry'
            snapshot = await post('heartbeat', ttl=30)
            require(snapshot.get('source_fresh') is True, 'source_heartbeat_not_fresh')
            require(snapshot.get('task_fresh') is False and snapshot.get('status') == 'unknown',
                    'expired_task_revived')
            require('state' not in snapshot and snapshot.get('last_state') == 'running',
                    'expired_task_exposed_as_current_state')
            require(snapshot['task_updated_at'] == task_updated_at
                    and snapshot['task_expires_at'] == task_expires_at,
                    'heartbeat_changed_task_lease')
            try:
                bridge.parse_source(snapshot)
            except bridge.SourceUnavailable:
                pass
            else:
                raise CheckFailed('bridge_accepted_expired_task')

            # While the BLE link remains connected, use the same no-write path as watch.
            # Cross firmware's 15-second TTL but only claim what the signed ACK exposes.
            report['stage'] = 'read_only_until_after_device_ttl'
            deadline = last_applied_monotonic + 16.2
            while True:
                result = await relay.step(client, config, None)
                require(result is None, 'stale_source_generated_write_ack')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(1, remaining))
            after = await bridge.read_info(client, config)
            require(after['nonce'] == ack['nonce'], 'watch_rebooted_during_expiry')
            require(all(after[key] == baseline[key] for key in baseline),
                    'stale_source_advanced_ble_sequence')
            report['freshness'] = {
                'task_ttl_seconds': 5,
                'source_heartbeat_sent_after_seconds': 6,
                'source_heartbeat_ttl_seconds': 30,
                'source_fresh_after_heartbeat': True,
                'task_fresh_after_heartbeat': False,
                'task_timestamp_and_expiry_unchanged': True,
                'expired_snapshot_rejected': True,
                'elapsed_since_last_applied_seconds': round(time.monotonic() - last_applied_monotonic, 3),
                'accepted_before': baseline['accepted'], 'accepted_after': after['accepted'],
                'applied_before': baseline['applied'], 'applied_after': after['applied'],
                'no_ble_sequence_growth_during_expiry': True,
                'same_authenticated_boot_session': True,
                'device_ttl_seconds': 15,
                'device_freshness_field_available': False,
                'device_stale_ui_visually_verified': False,
                'evidence_limit': 'Signed accepted/applied counters prove no refresh write; '
                                  'firmware v1 INFO does not expose freshness or rendered pixels.'}
            report['passed'] = True
            report['stage'] = 'complete'
    finally:
        if agent:
            agent.disconnect()


def write_report(path, report):
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Sanitized QA JSON output')
    parser.add_argument('--config', type=Path, default=bridge.CONFIG, help='Existing private pairing file')
    parser.add_argument('--timeout', type=float, default=180,
                        help='Whole BLE scenario timeout in seconds, from 45 to 300 (default 180)')
    args = parser.parse_args()
    if not 45 <= args.timeout <= 300:
        parser.error('--timeout must be between 45 and 300 seconds')
    report = dict(test='synthetic_http_hub_to_real_ble',
                  started_at=datetime.now(timezone.utc).isoformat(),
                  passed=False, stage='start_isolated_hub',
                  source='synthetic events; not actual Codex, Claude, or Cursor host hooks',
                  transport='real loopback HTTP + real BLE to existing USB-bound watch',
                  no_pairing_key_or_nonce_in_report=True,
                  automatic_reconnect_tested=False)
    exit_code = 1
    try:
        with tempfile.TemporaryDirectory(prefix='xueqiu-hub-ble-qa-') as folder:
            os.chmod(folder, 0o700)
            hub = StatusHub(Path(folder))
            token = secrets.token_urlsafe(32)
            server = HubServer(('127.0.0.1', 0), hub, token)
            worker = threading.Thread(target=server.serve_forever,
                                      kwargs={'poll_interval': .1}, daemon=True)
            worker.start()
            try:
                base_url = f'http://127.0.0.1:{server.server_port}'
                asyncio.run(asyncio.wait_for(exercise(args, base_url, token, hub.snapshot_path, report),
                                             timeout=args.timeout))
                exit_code = 0
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
                report['isolated_http_server_stopped'] = not worker.is_alive()
    except (Exception, KeyboardInterrupt) as error:
        exit_code = 1
        report['passed'] = False
        report['error_type'] = type(error).__name__
        # Only our fixed assertion codes are safe to publish; library errors may contain IDs.
        if isinstance(error, CheckFailed):
            report['failure_code'] = str(error)
        print(f'Test did not complete at {report["stage"]}: {type(error).__name__}', file=sys.stderr)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_report(args.output, report)
    print(f'Sanitized QA report: {args.output}', flush=True)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
