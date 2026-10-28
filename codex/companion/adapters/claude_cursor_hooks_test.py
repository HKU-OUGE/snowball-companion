"""Synthetic callbacks only: no real Claude/Cursor task or BLE connection."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from status_hub import HubServer, StatusHub

import claude_cursor_hook_common as common
import claude_hooks
import cursor_hooks


class HookMappings(unittest.TestCase):
    def test_claude_lifecycle_and_approval(self):
        for event, phase in {"SessionStart": "idle", "UserPromptSubmit": "thinking",
                             "PreToolUse": "running", "PermissionRequest": "waiting",
                             "PostToolUse": "thinking", "StopFailure": "failed"}.items():
            self.assertEqual(claude_hooks.map_event(event, {"session_id": "s"}), ("s", phase))
        self.assertEqual(claude_hooks.map_event("PreToolUse", {"session_id": "s", "tool_name": "AskUserQuestion"}), ("s", "waiting"))

    def test_stop_reports_response_end_independent_of_reply_text(self):
        for text in ("All tests passed", "I still need your input", "The project is incomplete"):
            self.assertEqual(claude_hooks.map_event("Stop", {"session_id": "s", "last_assistant_message": text}), ("s", "completed"))

    def test_prior_stop_continuation_or_background_work_does_not_hide_response_end(self):
        self.assertEqual(claude_hooks.map_event("Stop", {"session_id": "s", "stop_hook_active": True}), ("s", "completed"))
        self.assertEqual(claude_hooks.map_event("Stop", {"session_id": "s", "background_tasks": [{}], "session_crons": [{}]}), ("s", "completed"))

    def test_new_activity_supersedes_stop_completion(self):
        events = ("Stop", "UserPromptSubmit", "PreToolUse")
        self.assertEqual([claude_hooks.map_event(event, {"session_id": "s"})[1] for event in events], ["completed", "thinking", "running"])

    def test_subagents_and_unknown_events_are_ignored(self):
        self.assertIsNone(claude_hooks.map_event("Stop", {"session_id": "s", "agent_id": "child"}))
        self.assertIsNone(claude_hooks.map_event("SubagentStop", {"session_id": "s"}))
        self.assertIsNone(cursor_hooks.map_event("afterAgentResponse", {"conversation_id": "s", "text": "done"}))

    def test_tool_error_does_not_claim_turn_failed(self):
        self.assertEqual(claude_hooks.map_event("PostToolUseFailure", {"session_id": "s"}), ("s", "thinking"))
        self.assertEqual(cursor_hooks.map_event("postToolUseFailure", {"conversation_id": "s"}), ("s", "thinking"))

    def test_cursor_requires_explicit_terminal_status(self):
        for status, phase in {"completed": "completed", "error": "failed", "aborted": "idle"}.items():
            self.assertEqual(cursor_hooks.map_event("stop", {"conversation_id": "s", "status": status}), ("s", phase))
        self.assertIsNone(cursor_hooks.map_event("stop", {"conversation_id": "s"}))
        self.assertIsNone(cursor_hooks.map_event("stop", {"conversation_id": "s", "status": "unknown"}))

    def test_cursor_config_excludes_permission_hooks(self):
        events = cursor_hooks.fragment()["hooks"]
        forbidden = {"preToolUse", "beforeShellExecution", "beforeMCPExecution", "beforeReadFile", "beforeTabFileRead", "subagentStart"}
        self.assertFalse(set(events) & forbidden)

    def test_claude_never_writes_permission_decision(self):
        with patch.object(claude_hooks, "run_hook"), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(claude_hooks.main(["--event", "PermissionRequest"]), 0)
        self.assertEqual(output.getvalue(), "")

    def test_cursor_does_not_block_if_telemetry_fails(self):
        with patch.object(cursor_hooks, "run_hook", return_value=False), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cursor_hooks.main(["--event", "beforeSubmitPrompt"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"continue": True})

    def test_bounded_malformed_input_and_event_mismatch(self):
        with self.assertRaises(ValueError):
            common.read_input(io.BytesIO(b"x" * (common.INPUT_LIMIT + 1)))
        with patch.object(common, "emit") as send:
            for payload in (b"not-json", b"[]", b'{"hook_event_name":"Stop","session_id":"s"}'):
                self.assertFalse(common.run_hook(claude_hooks.map_event, "Claude", "claude-code-hooks", "PreToolUse", stream=io.BytesIO(payload)))
            send.assert_not_called()


class PrivateTransport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        token = self.root / "hub-token"
        token.write_text("synthetic-test-token")
        token.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_allowlist_and_monotonic_sequence(self):
        sent = []
        for _ in range(2):
            common.emit("Claude", "claude-code-hooks", "private-session-value", "thinking", config_dir=self.root, sender=lambda e, t: sent.append(e))
        self.assertEqual([e["seq"] for e in sent], [1, 2])
        self.assertEqual(set(sent[0]), {"version", "provider", "source", "session_id", "event_id", "seq", "timestamp", "kind", "phase", "ttl_seconds"})
        self.assertNotIn("private-session-value", json.dumps(sent))
        self.assertNotEqual(sent[0]["event_id"], sent[1]["event_id"])
        states = list((self.root / "hook-state/claude-code-hooks").glob("*.json"))
        self.assertEqual(len(states), 1)
        self.assertEqual(json.loads(states[0].read_text()), {"seq": 2})
        self.assertEqual(states[0].stat().st_mode & 0o777, 0o600)

    def test_concurrent_callbacks_do_not_duplicate_or_reverse_sequences(self):
        sent, failures = [], []
        def worker():
            try:
                common.emit("Claude", "claude-code-hooks", "s", "running", config_dir=self.root, sender=lambda e, t: sent.append(e["seq"]))
            except Exception as error:
                failures.append(type(error).__name__)
        threads = [threading.Thread(target=worker) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(sent, list(range(1, 13)))

    def test_prompts_commands_and_paths_are_not_forwarded(self):
        payload = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash",
                   "tool_input": {"command": "SECRET-COMMAND", "token": "SECRET-TOKEN"},
                   "prompt": "SECRET-PROMPT", "cwd": "/SECRET-PATH", "transcript_path": "/never/read"}
        with patch.object(common, "emit") as send:
            common.run_hook(claude_hooks.map_event, "Claude", "claude-code-hooks", "PreToolUse", stream=io.BytesIO(json.dumps(payload).encode()))
        self.assertEqual(send.call_args.args, ("Claude", "claude-code-hooks", "s", "running"))

    def test_public_token_and_symlink_rejected(self):
        (self.root / "hub-token").chmod(0o644)
        with self.assertRaises(ValueError):
            common.emit("Claude", "claude-code-hooks", "s", "idle", config_dir=self.root)
        (self.root / "hub-token").unlink()
        (self.root / "hub-token").symlink_to(self.root / "other")
        with self.assertRaises(OSError):
            common.emit("Claude", "claude-code-hooks", "s", "idle", config_dir=self.root)

    def test_failed_delivery_does_not_replay_old_state(self):
        def unavailable(e, t):
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            common.emit("Claude", "claude-code-hooks", "s", "running", config_dir=self.root, sender=unavailable)
        event = common.emit("Claude", "claude-code-hooks", "s", "idle", config_dir=self.root, sender=lambda e, t: None)
        self.assertEqual(event["seq"], 2)


class ConfigGeneration(unittest.TestCase):
    def test_merge_preserves_config_and_is_idempotent(self):
        original = {"permissions": {"defaultMode": "default"}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing-hook"}]}]}}
        merged = common.merge_hooks(original, claude_hooks.fragment())
        self.assertEqual(original["hooks"]["Stop"], merged["hooks"]["Stop"][:1])
        self.assertEqual(original["permissions"], merged["permissions"])
        self.assertEqual(merged, common.merge_hooks(merged, claude_hooks.fragment()))
        self.assertEqual(len(original["hooks"]["Stop"]), 1)

    def test_generation_only_creates_review_file(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "preview.json"
            process = subprocess.run([sys.executable, str(Path(claude_hooks.__file__)), "config", "--output", str(output)], capture_output=True)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            again = subprocess.run([sys.executable, str(Path(claude_hooks.__file__)), "config", "--output", str(output)], capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            live = subprocess.run([sys.executable, str(Path(cursor_hooks.__file__)), "config", "--output", str(Path(temp) / "hooks.json")], capture_output=True)
            self.assertNotEqual(live.returncode, 0)
            self.assertFalse((Path(temp) / "hooks.json").exists())


class HubIntegration(unittest.TestCase):
    def test_real_loopback_transport_and_rejected_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = root / "hub-token"
            token.write_text("synthetic-hub-token")
            token.chmod(0o600)
            hub = StatusHub(root / "hub")
            server = HubServer(("127.0.0.1", 0), hub, "synthetic-hub-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(common, "HUB_URL", f"http://127.0.0.1:{server.server_port}/v1/events"):
                    common.emit("Claude", "claude-code-hooks", "synthetic-claude", "waiting", config_dir=root)
                    self.assertEqual(hub.snapshot()["state"], "waiting")
                    common.emit("Cursor", "cursor-hooks", "synthetic-cursor", "completed", config_dir=root)
                    snapshots = hub.snapshot()["sessions"]
                    self.assertEqual({entry["provider"] for entry in snapshots}, {"Claude", "Cursor"})
                    token.write_text("wrong-test-credential")
                    with self.assertRaises(urllib.error.HTTPError) as result:
                        common.emit("Cursor", "cursor-hooks", "synthetic-cursor", "failed", config_dir=root)
                    self.assertEqual(result.exception.code, 401)
                    self.assertNotIn("failed", [entry.get("last_state") for entry in hub.snapshot()["sessions"]])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
