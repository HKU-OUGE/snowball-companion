"""Offline fixture regression; never pairs devices, sends chats or installs services."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPANION = [
    'test_answers', 'test_attention', 'test_bridge_source', 'test_catalog',
    'test_info_transport', 'test_pet_hub', 'test_pet_sync', 'test_power_telemetry',
    'test_priority_queue', 'test_private_files', 'test_protocol', 'test_reconnect',
    'test_session_capacity', 'test_sidebar_tasks', 'test_speech',
    'test_speech_relay', 'test_speech_scheduling', 'test_status_hub',
    'test_windows', 'test_windows_app_pipe', 'test_portable_imports',
]

def main():
    env = dict(os.environ, PYTHONUTF8='1')
    commands = [
        (ROOT / 'codex/companion', ['-m', 'unittest', *COMPANION]),
        (ROOT, ['-m', 'pytest', '-q', 'codex/tests']),
        (ROOT, ['-m', 'pytest', '-q', 'codex/tools']),
        (ROOT, ['-m', 'pytest', '-q', 'tools/test_plugin_bundles.py']),
    ]
    for cwd, args in commands:
        subprocess.run([sys.executable, '-X', 'utf8', *args], cwd=cwd, env=env, check=True)

if __name__ == '__main__':
    main()
