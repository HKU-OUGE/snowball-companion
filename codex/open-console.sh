#!/usr/bin/env sh
set -eu
snowball_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$snowball_root/.venv/bin/python" ]; then
    exec "$snowball_root/.venv/bin/python" "$snowball_root/companion/console_launcher.py" open "$@"
fi
exec python3 "$snowball_root/companion/console_launcher.py" open "$@"
