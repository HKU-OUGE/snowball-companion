#!/usr/bin/env python3
"""Claude Code observational hooks (CLI and local Desktop Code sessions)."""
import argparse
import sys
from claude_cursor_hook_common import config_main, hook_command, run_hook

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
          "PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd")


def map_event(event, payload):
    # A subagent tool or stop must not overwrite its parent's visible state.
    if payload.get("agent_id"):
        return None
    phase = {
        "SessionStart": "idle", "UserPromptSubmit": "thinking",
        "PreToolUse": "running", "PermissionRequest": "waiting",
        "PostToolUse": "thinking", "PostToolUseFailure": "thinking",
        "Stop": "completed", "StopFailure": "failed", "SessionEnd": "idle",
    }.get(event)
    if phase is None:
        return None
    # AskUserQuestion is an explicit interaction, not a generic tool approval.
    if event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
        phase = "waiting"
    # Stop means this main-agent response ended, not that a project succeeded.
    # Other Stop hooks may still request continuation; the next actual activity
    # event supersedes this state. stop_hook_active describes prior continuation.
    return payload.get("session_id"), phase


def fragment(script=__file__):
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": hook_command(script, event), "timeout": 2}]}]
                      for event in EVENTS}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "config":
        config_main(__file__, fragment, argv[1:])
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", choices=EVENTS, required=True)
    args = parser.parse_args(argv)
    run_hook(map_event, "Claude", "claude-code-hooks", args.event)
    # Empty stdout and exit 0 leave Claude's normal permission decision intact.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
