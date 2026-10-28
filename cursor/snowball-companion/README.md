# Snowball Companion for Cursor

安装、状态语义、配对、权限和已知限制请参见随 ZIP 提供的顶层 `README.md`。插件只观察 Cursor Agent 生命周期，复用已安装的 Snowball Hub；不会更改 Cursor 权限决策。

检查当前电脑的 Hub：`python3 scripts/control.py doctor`。列出角色：`python3 scripts/control.py characters`。仅在用户明确选择角色后才执行 `python3 scripts/control.py pet <角色 ID>`。
