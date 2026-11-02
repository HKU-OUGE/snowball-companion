# Windows 支持检查 · 2026-11-03

范围为当前公开 Codex 安装包及其完整发行文件清单。历史 `companion/adapters/` 目录不在 Codex ZIP 内，不能拿旧脚本替代安装后的活动 Hook。Claude／Cursor 独立包仍为 POSIX 实现，不把 Codex 的 Windows 结论扩展过去。

## 本轮修复

- Windows 原子状态发布存在实际并发问题：普通读取与文件替换会遇到拒绝访问；`MoveFileEx` 也不能满足保持旧句柄时替换的测试。改用 `ReplaceFileW`，配合允许删除共享的读取句柄和最长 250ms 的短暂缺失／共享冲突重试。
- 原生文件锁、DACL、目录联接拒绝、UTF-8 Hook 启动、命名管道与后台监督器保持专用实现；Linux 管理入口在 Windows 提前拒绝。
- 回归测试分别验证 Unix shell／signal 和 Windows 原生安装，Windows 不再用 POSIX mode 位判断文件安全；DACL 仍由实际 Windows API 读回验证。

## 可复现验证

GitHub Actions 工作流 `Codex Windows and Linux` 在 Windows 与 Ubuntu 上分别运行 Python 3.12、3.13。运行 `python tools/check_codex_portable.py` 可复现：实际发行脚本导入、运行时测试、更新协议、Hook／安装器和完整／Core ZIP 测试。全部使用临时目录、测试密钥及隔离事件；不访问真实设备或发送真实聊天。

首次连接、登录自启、Hook 到 BLE、人工录音与问题投递的已有实机记录见 [2026-11-02 Windows 验证](WINDOWS-QA-2026-11-02.md)。本轮自动化测试不替代用户那台 Windows 电脑的实际蓝牙、冷启动、休眠和 Wi-Fi／OTA 验收。

本轮四组平台测试均通过：[CI 验证记录](https://github.com/HKU-OUGE/snowball-companion/actions/runs/41495688355)。Windows 原生 API 测试实际执行；Unix 专用测试单独运行于 Ubuntu。

## 明确的剩余限制

- Windows 登录任务不会自动发现 Codex Desktop 重启后变化的应用命名管道端点，需在当前宿主环境重启 Hub 并核对送达能力。基础 Hook 状态与语音投递是不同通路。
- 原生 Windows 的独立空闲证明尚未接通，不能授权设备自动安装更新。
- 普通安装不下载语音模型；识别与消息发送需要各自就绪。
- 升级电脑插件不会更新设备 SD 安装盘内旧包，也不会自动更新设备固件。
