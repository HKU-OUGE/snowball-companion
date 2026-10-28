---
description: 检查 Snowball Companion 插件与本机设备同步服务
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/control.py" doctor` and explain the actual result in the user's language. This command is read-only. A healthy Hub does not prove device connectivity.

For an existing Snowball installation, reuse its Hub, BLE, Wi-Fi and cloud services. Do not restart or replace those services, alter credentials, install firmware, or change session priorities during setup. Tell the user to select Claude in the device's Settings; only sessions observed after enabling this plugin will appear.

If no Hub exists, consult `${CLAUDE_PLUGIN_ROOT}/README.md`. The bundled `scripts/run_hub.py` can run a local foreground Hub after the user asks to start it; it does not connect to another AI app. Bluetooth pairing is a separate explicit action. Do not run lifecycle hooks with fabricated events against the user's real device.

The plugin supports local Claude Code Desktop/CLI on Linux and macOS with Python 3.10+. It does not install ChatGPT Pets inside Claude, handle ordinary Chat sessions, forward remote/SSH/cloud hooks to this computer, approve permissions, answer questions, or send voice instructions back to Claude. Report these boundaries accurately.
