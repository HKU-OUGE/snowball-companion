"""Authenticated local agent bridge for setup progress, not an arbitrary command executor."""
import argparse
import http.client
import json
from pathlib import Path
import time

from private_files import private_write, read_text
from console_launcher import STATE

RECEIPT = STATE / 'setup-agent.json'


def request(data, state=STATE):
    value = json.loads(read_text(state / 'server.json'))
    if value.get('protocol') != 1 or type(value.get('port')) is not int or not 1024 <= value['port'] <= 65535:
        raise RuntimeError('console_entry_invalid')
    token = value.get('token')
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise RuntimeError('console_entry_invalid')
    connection = http.client.HTTPConnection('127.0.0.1', value['port'], timeout=10)
    try:
        connection.request('POST', '/agent/setup', json.dumps(data), {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse();raw = response.read(32769)
        if len(raw) > 32768:
            raise RuntimeError('setup_response_invalid')
        result = json.loads(raw)
        if response.status != 200:
            raise RuntimeError(result.get('error', 'setup_unavailable'))
        return result
    finally:
        connection.close()


def begin(adapter):
    value = request({'operation': 'attach', 'adapter': adapter})
    if not isinstance(value.get('run'), str) or not isinstance(value.get('grant'), str):
        raise RuntimeError('setup_response_invalid')
    private_write(RECEIPT, json.dumps({'run': value['run'], 'grant': value['grant']}).encode())
    return value['status']


def update(operation='status', **extra):
    value = json.loads(read_text(RECEIPT))
    return request({'operation': operation, 'run': value['run'], 'grant': value['grant'], **extra})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'watch', 'pet'))
    parser.add_argument('--state', choices=('working', 'verified', 'needs_auth', 'failed'))
    parser.add_argument('--character', default='auto')
    parser.add_argument('--seconds', type=int, default=25)
    args = parser.parse_args()
    if args.command == 'pet':
        if not args.state:parser.error('--state required')
        result = update('pet', state=args.state, character=args.character)
    else:
        result = update()
        if args.command == 'watch':
            until = time.monotonic() + max(1, min(30, args.seconds))
            prior = json.dumps(result.get('steps'), sort_keys=True)
            while not result.get('complete') and time.monotonic() < until:
                time.sleep(2)
                result = update()
                if json.dumps(result.get('steps'), sort_keys=True) != prior:
                    break
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
