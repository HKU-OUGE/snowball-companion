#!/usr/bin/env python3
"""Compatibility entrypoint for the repository plugin-only release builder."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from build_plugin_bundles import main
if __name__ == '__main__':
    raise SystemExit(main(['--platform', 'claude', *sys.argv[1:]]))
