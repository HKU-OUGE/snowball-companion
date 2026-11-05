---
description: 打开雪球控制台，自动准备连接、语音和登录后台
---

For a first-use or full setup request, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/open_console.py" --setup` (native Windows: `python -X utf8`). Resolve the real bundled console first; the complete distribution has `console/`. Do not invent a developer localhost URL or start a second Hub. Opening for inspection alone omits `--setup`.

The webpage owns choices, progress, retries and skipping: 自动配置 → 连接雪球 → 确认同步 → 网络设置. The plan prepares dependencies, current-user login startup, connection services and local speech. Reuse existing services, model and pairing. Continue monitoring via `scripts/open_console.py --setup-watch`; do not stop merely because a browser opened. Account ChatGPT Pet installation is not applicable to this adapter and must not be claimed as completed.

User stays in the webpage except for firmware-required physical pairing confirmation and the later device gesture practice. Prepare dependencies, identify the actual USB port and attempt binding first. Firmware 0.27.0+ opens pairing automatically during first-use setup. Only the explicit usb_pairing_confirmation_required result warrants a device tap; startup delay, serial silence and access errors do not. Keep the user in the webpage for all other connection steps. OS paired status alone is insufficient. Wi-Fi is optional; no separate chat checklist for speech or autostart.

For read-only diagnosis use `scripts/control.py doctor`. Report actual host events, Hub health, transport authentication and device acknowledgements separately; never inject fake tasks. No firmware writes, credential reset, hook trust edits, or session-priority changes are part of setup. Unknown service ownership must be resolved, not replaced.

The console runs on native Windows, Linux and macOS, but this adapter's Hooks retain their documented platform support. Local speech readiness does not promise answering or sending voice instructions to Claude Code. Preserve those capability boundaries. If the host does not expose a supported delivery channel, explain that limit in the setup result.

The v0.7.0 native installer also offers an agent-free feature-selection page. Respect existing installer choices and do not start another setup in parallel. Guide: https://github.com/HKU-OUGE/snowball-companion/blob/main/installer/README.md
