#!/usr/bin/env bash
set -euo pipefail
market_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v codex >/dev/null 2>&1; then
  codex_bin="$(command -v codex)"
elif [ -x /usr/lib/chatgpt/resources/codex ]; then
  codex_bin=/usr/lib/chatgpt/resources/codex
else
  echo "Codex CLI not found. Install Codex or add its CLI to PATH." >&2
  exit 1
fi
exec python3 "$market_root/tools/install_plugin.py" --codex "$codex_bin" --market-root "$market_root"
