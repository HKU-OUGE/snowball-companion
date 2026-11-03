---
description: 打开雪球控制台，配置设备连接与 Wi-Fi
---

实体设备首次使用以**电脑雪球控制台**为主线：准备电脑 → 连接雪球 → 确认同步 → 可选功能。
每次只给当前一步，明确“现在看电脑控制台”或“现在看雪球”，并告诉用户完成后回到哪里。
先准备依赖，再去设备「连接电脑」点「开启配对」；随后回控制台核对串口并绑定。
不要只指向系统蓝牙设置。确认真实同步后回设备继续摸摸、吃西瓜和手势练习；
Wi-Fi 可跳过，语音和账号桌宠也不应成为基础连接的门槛。
教程练习、Hub 健康、BLE 认证和真实任务回执分别核验，不通过模拟事件宣布成功。

For device setup or Wi-Fi provisioning, run `python3 "${CURSOR_PLUGIN_ROOT}/scripts/open_console.py"`
(native Windows: `python -X utf8`). It opens the recipient's registered local console or locates it
inside the complete extracted distribution. If the entry is missing, direct the user to the full
package's `console/Open-Snowball.cmd` on Windows or `console/open-console.sh` on Linux/macOS.
Keep the complete package in a fixed folder. Do not assume a developer-only localhost port exists.

The console guides dependency preparation, USB identity binding, Bluetooth, optional Wi-Fi and
actual connection checks. Existing pairing and shared services are reused. It does not require
installing Codex, does not flash firmware, and does not extend this adapter's native Windows Hook
support. For status-only diagnosis, use `scripts/control.py doctor` and report its real result.
