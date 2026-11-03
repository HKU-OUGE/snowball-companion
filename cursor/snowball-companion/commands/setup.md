---
description: 打开雪球控制台，配置设备连接与 Wi-Fi
---

For device setup or Wi-Fi provisioning, run `python3 "${CURSOR_PLUGIN_ROOT}/scripts/open_console.py"`
(native Windows: `python -X utf8`). It opens the recipient's registered local console or locates it
inside the complete extracted distribution. If the entry is missing, direct the user to the full
package's `console/Open-Snowball.cmd` on Windows or `console/open-console.sh` on Linux/macOS.
Keep the complete package in a fixed folder. Do not assume a developer-only localhost port exists.

The console guides dependency preparation, USB identity binding, Bluetooth, optional Wi-Fi and
actual connection checks. Existing pairing and shared services are reused. It does not require
installing Codex, does not flash firmware, and does not extend this adapter's native Windows Hook
support. For status-only diagnosis, use `scripts/control.py doctor` and report its real result.
