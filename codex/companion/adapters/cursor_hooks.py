#!/usr/bin/env python3
"""Cursor local Agent hooks; deliberately exclude tool permission hooks."""
import argparse
import sys
from claude_cursor_hook_common import config_main, hook_command, run_hook

EVENTS = ("sessionStart", "beforeSubmitPrompt", "postToolUse", "postToolUseFailure",
          "preCompact", "stop", "sessionEnd")


def map_event(event, payload):
    phase = {"sessionStart": "idle", "beforeSubmitPrompt": "thinking",
             "postToolUse": "thinking", "postToolUseFailure": "thinking",
             "preCompact": "thinking", "sessionEnd": "idle"}.get(event)
    if event == "stop":
        # Explicit final status is authoritative only for this turn, not project success.
        phase = {"completed": "completed", "error": "failed", "aborted": "idle"}.get(payload.get("status"))
    if event == "sessionEnd" and payload.get("reason") == "error":
        phase = "failed"
    if phase is None:
        return None
    return payload.get("conversation_id") or payload.get("session_id"), phase


def fragment(script=__file__):
    return {"version": 1, "hooks": {event: [{"command": hook_command(script, event), "timeout": 2,
                                               "failClosed": False}] for event in EVENTS}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "config":
        config_main(__file__, fragment, argv[1:])
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", choices=EVENTS, required=True)
    args = parser.parse_args(argv)
    run_hook(map_event, "Cursor", "cursor-hooks", args.event)
    # Prompt submission keeps its normal path; no tool permission decisions.
    print('{"continue":true}' if args.event == "beforeSubmitPrompt" else '{}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
