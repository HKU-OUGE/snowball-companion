---
description: 打开雪球控制台，自动准备连接、语音和登录后台
---

For a first-use or full setup request, run `python3 "${CURSOR_PLUGIN_ROOT}/scripts/open_console.py" --setup` (native Windows: `python -X utf8`). Resolve the real bundled console first; the complete distribution has `console/`. Do not invent a developer localhost URL or start a second Hub. Opening for inspection alone omits `--setup`.

The webpage owns choices, progress, retries and skipping: 自动配置 → 连接雪球 → 确认同步 → 网络设置. The plan prepares dependencies, current-user login startup, connection services and local speech. Reuse existing services, model and pairing. Continue monitoring via `scripts/open_console.py --setup-watch`; do not stop merely because a browser opened. Account ChatGPT Pet installation is not applicable to this adapter and must not be claimed as completed.

User stays in the webpage except for firmware-required physical pairing confirmation and the later device gesture practice. Prepare dependencies before telling the user to open the two-minute pairing window. Match the real Bluetooth name and USB port; after the device's 开启配对, return to the webpage. OS paired status alone is insufficient. Wi-Fi is optional; no separate chat checklist for speech or autostart.

For read-only diagnosis use `scripts/control.py doctor`. Report actual host events, Hub health, transport authentication and device acknowledgements separately; never inject fake tasks. No firmware writes, credential reset, hook trust edits, or session-priority changes are part of setup. Unknown service ownership must be resolved, not replaced.

The console runs on native Windows, Linux and macOS, but this adapter's Hooks retain their documented platform support. Local speech readiness does not promise answering or sending voice instructions to Cursor. Preserve those capability boundaries. If the host does not expose a supported delivery channel, explain that limit in the setup result.
