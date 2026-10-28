"""Private, bounded, loopback-only transport for observational AI hooks.

Only allowlisted lifecycle state crosses this module's transport boundary.
This module never opens transcripts, sends tool arguments, or approves actions.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import time
import urllib.request
import uuid

INPUT_LIMIT = 65536
HUB_URL = "http://127.0.0.1:4190/v1/events"
PHASES = {"idle", "thinking", "running", "waiting", "completed", "failed"}


def read_input(stream):
    raw = stream.read(INPUT_LIMIT + 1)
    if len(raw) > INPUT_LIMIT:
        raise ValueError("hook input too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("hook input must be object")
    return result


def opaque_session(source, session):
    if not isinstance(session, str) or not session or len(session) > 512:
        raise ValueError("missing or invalid session id")
    return hashlib.sha256((source + "\0" + session).encode()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_event(event, token):
    # Ignore proxy environment variables, reject redirects, use only literal loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        HUB_URL, data=json.dumps(event, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    with opener.open(request, timeout=0.35) as response:
        if response.status not in (200, 201, 202, 204):
            raise ValueError("hub did not accept event")
        response.read(1024)


def _private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("untrusted hook state directory")
    os.chmod(path, 0o700)


def _open_private(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError("expected owner-only regular file")
    return fd


def emit(provider, source, session, phase, *, config_dir=None, sender=send_event):
    if provider not in ("Claude", "Cursor") or phase not in PHASES:
        raise ValueError("unsupported provider or phase")
    if source not in ("claude-code-hooks", "cursor-hooks"):
        raise ValueError("unsupported source")
    sid = opaque_session(source, session)
    root = Path(config_dir) if config_dir is not None else Path.home() / ".config/xueqiu"
    # Hub creates this token; do not invent a second credential or print it.
    with os.fdopen(_open_private(root / "hub-token", os.O_RDONLY), "r") as token_file:
        token = token_file.read(4097).strip()
    if not token or len(token) > 4096 or any(c.isspace() for c in token):
        raise ValueError("invalid hub credential")
    folder = root / "hook-state" / source
    _private_dir(root / "hook-state")
    _private_dir(folder)
    with os.fdopen(_open_private(folder / (sid + ".json"), os.O_RDWR | os.O_CREAT), "r+") as state:
        # Lock spans counter reservation and delivery, so concurrent callbacks do
        # not POST sequence N+1 before N. Hooks never retry an old event later.
        fcntl.flock(state, fcntl.LOCK_EX)
        previous = state.read(128)
        seq = json.loads(previous)["seq"] + 1 if previous else 1
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("invalid counter")
        state.seek(0)
        json.dump({"seq": seq}, state)
        state.truncate()
        state.flush()
        event = {"version": 1, "provider": provider, "source": source,
                 "session_id": sid, "event_id": str(uuid.uuid4()), "seq": seq,
                 "timestamp": time.time(), "kind": "task", "phase": phase,
                 "ttl_seconds": 30}
        sender(event, token)
        return event


def merge_hooks(existing, fragment):
    """Return a new settings object; preserve existing hooks and unrelated keys."""
    merged = copy.deepcopy(existing)
    if not isinstance(merged, dict) or not isinstance(merged.get("hooks", {}), dict):
        raise ValueError("invalid existing hook configuration")
    if "version" in fragment:
        if merged.get("version", fragment["version"]) != fragment["version"]:
            raise ValueError("unsupported existing hook schema version")
        merged.setdefault("version", fragment["version"])
    hooks = merged.setdefault("hooks", {})
    for name, additions in fragment["hooks"].items():
        entries = hooks.setdefault(name, [])
        if not isinstance(entries, list):
            raise ValueError("existing event handlers must be arrays")
        for handler in additions:
            if handler not in entries:
                entries.append(copy.deepcopy(handler))
    return merged


def hook_command(script, event):
    return " ".join(shlex.quote(value) for value in (sys.executable, str(Path(script).resolve()), "--event", event))


def config_main(script, make_fragment, argv):
    parser = argparse.ArgumentParser(description="Generate reviewable hooks configuration; never install it.")
    parser.add_argument("--existing", type=Path, help="Read existing settings and preserve all fields")
    parser.add_argument("--output", type=Path, required=True, help="New file only; existing files are never overwritten")
    args = parser.parse_args(argv)
    existing = json.loads(args.existing.read_text()) if args.existing else {}
    result = merge_hooks(existing, make_fragment(script))
    # Explicitly refuse the standard live configuration names. The user reviews
    # and installs the generated copy; generation cannot switch hooks on.
    if args.output.name in {"settings.json", "settings.local.json", "hooks.json"}:
        parser.error("choose a review filename, such as xueqiu-hooks.preview.json")
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write("\n")


def run_hook(mapper, provider, source, event, *, stream=None):
    try:
        payload = read_input(stream or sys.stdin.buffer)
        # Pin event to the configured hook. A mismatch never becomes telemetry.
        if payload.get("hook_event_name", event) != event:
            return False
        mapping = mapper(event, payload)
        if mapping is not None:
            session, phase = mapping
            emit(provider, source, session, phase)
            return True
    except Exception:
        # Raw hook JSON may contain secrets. No payload/error repr is logged.
        # Telemetry failure must not block, approve or modify the host task.
        return False
    return False
