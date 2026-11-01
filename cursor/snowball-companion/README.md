# Snowball Companion for Cursor

## 0.2.1：设备手动更新请求

支持新固件发起的手动检查和确认版本下载请求，蓝牙优先；电脑端继续兼容旧更新协议。检查只返回版本与公告，确认后才请求对应镜像，不静默替换已确认版本。Wi-Fi 回退由兼容设备固件处理。旧固件不会因安装插件自动获得新请求界面；本次包准备与主机测试不代表已完成实机验收。

安装、状态语义、配对、权限和已知限制请参见随 ZIP 提供的顶层 `README.md`。插件只观察 Cursor Agent 生命周期，复用已安装的 Snowball Hub；不会更改 Cursor 权限决策。

检查当前电脑的 Hub：`python3 scripts/control.py doctor`。列出角色：`python3 scripts/control.py characters`。仅在用户明确选择角色后才执行 `python3 scripts/control.py pet <角色 ID>`。
