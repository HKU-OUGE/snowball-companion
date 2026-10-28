#!/usr/bin/env bash
set -euo pipefail
bridge_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ "$#" -eq 0 ]; then set -- control; fi
bridge_entry="$bridge_dir/bridge.py"
case "$1" in start|stop|status) bridge_entry="$bridge_dir/manage.py" ;; esac
if command -v uv >/dev/null 2>&1; then
  bridge_env="${XDG_CACHE_HOME:-$HOME/.cache}/xueqiu/ble-venv"
  if [ ! -x "$bridge_env/bin/python" ]; then
    uv venv --python python3 "$bridge_env"
  fi
  if ! "$bridge_env/bin/python" -c 'import bleak,serial; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; from importlib.metadata import version; assert version("bleak")=="3.0.2"; assert version("cryptography")=="50.0.2"' >/dev/null 2>&1; then
    uv pip install --python "$bridge_env/bin/python" -r "$bridge_dir/requirements.txt"
  fi
  exec "$bridge_env/bin/python" "$bridge_entry" "$@"
fi
exec python3 "$bridge_entry" "$@"
