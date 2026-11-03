---
description: 检查 Snowball Companion 插件与本机设备同步服务
---

实体设备首次使用以**电脑雪球控制台**为主线：准备电脑 → 连接雪球 → 确认同步 → 可选功能。
每次只给当前一步，明确“现在看电脑控制台”或“现在看雪球”，并告诉用户完成后回到哪里。
先准备依赖，再去设备「连接电脑」点「开启配对」；随后回控制台核对串口并绑定。
不要只指向系统蓝牙设置。确认真实同步后回设备继续摸摸、吃西瓜和手势练习；
Wi-Fi 可跳过，语音和账号桌宠也不应成为基础连接的门槛。
教程练习、Hub 健康、BLE 认证和真实任务回执分别核验，不通过模拟事件宣布成功。

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/control.py" doctor` and explain the actual result in the user's language. This command is read-only. A healthy Hub does not prove device connectivity.

For an existing Snowball installation, reuse its Hub, BLE, Wi-Fi and cloud services. Do not restart or replace those services, alter credentials, install firmware, or change session priorities during setup. Tell the user to select Claude in the device's Settings; only sessions observed after enabling this plugin will appear.

If no Hub exists, consult `${CLAUDE_PLUGIN_ROOT}/README.md`. The bundled `scripts/run_hub.py` can run a local foreground Hub after the user asks to start it; it does not connect to another AI app. Bluetooth pairing is a separate explicit action. Do not run lifecycle hooks with fabricated events against the user's real device.

The plugin supports local Claude Code Desktop/CLI on Linux and macOS with Python 3.10+. It does not install ChatGPT Pets inside Claude, handle ordinary Chat sessions, forward remote/SSH/cloud hooks to this computer, approve permissions, answer questions, or send voice instructions back to Claude. Report these boundaries accurately.

For hardware setup or Wi-Fi, use the bundled device console described in the full package docs/CONSOLE.md. In the extracted Claude ZIP, the entry is console/Open-Snowball.cmd on Windows or console/open-console.sh on POSIX. If a shared console was already registered, reuse it. The console does not require installing Codex, and its Windows support does not broaden this adapter's Hook support. Do not give a developer-only fixed localhost URL.

To open the device console, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/open_console.py"` (Windows: `python -X utf8`). A missing entry must be resolved from the complete distribution, not by inventing a localhost URL.
