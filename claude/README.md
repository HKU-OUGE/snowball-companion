# Snowball Companion · Claude Code 插件包

插件 ID：`snowball-companion@snowball-local`，版本 `0.1.0`。

解压后把整个文件夹放在一个固定位置。在该目录打开终端，执行：

```sh
claude plugin marketplace add .
claude plugin install snowball-companion@snowball-local --scope user
```

然后新开 Claude Code Desktop 的本地 Code 会话，运行 `/snowball-companion:setup`。Desktop 与 CLI 共用插件配置；也可在 Desktop 提示框旁的 `+ → Add plugins` 中管理。已有会话可尝试 `/reload-plugins`，新会话是最直接的加载验证方式。

仅临时试用、不登记 marketplace：

```sh
claude --plugin-dir ./plugins/snowball-companion
```

已有 Snowball Hub 和设备同步服务的电脑可以直接复用；插件不会修改 Codex 的设置或刷写设备。

卸载只删除 Claude 插件：

```sh
claude plugin uninstall snowball-companion@snowball-local
claude plugin marketplace remove snowball-local
```

共享的 Snowball Hub、配对凭据、设备与其他软件集成继续保留。插件状态位于用户的 `~/.config/xueqiu/hook-state/claude-code-plugin/`，不放在插件缓存中。

完整功能、前提、状态语义、数据边界与首次设备配置见 [插件说明](plugins/snowball-companion/README.md)。验证边界见 `VALIDATION.json`。ZIP 不包含账号、会话记录、Wi-Fi 密码、设备密钥或签名私钥。
