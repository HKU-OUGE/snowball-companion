# Snowball Companion · Claude Code 插件包

## 0.2.0 更新

配套公共运行时与 Codex 0.5.0 同步，包含认证蓝牙更新客户端；Claude Hooks、命令和入口保持独立。单独启动的 Hub 不启用 Codex 活动轮询，没有可靠空闲依据时不自动安装。详见 [新手指南](../docs/START-HERE.md) 和 [更新说明](../docs/UPDATES.md)。新增代码与打包测试不代表 Claude 真实登录会话或实体设备已验收。

插件 ID：`snowball-companion@snowball-local`，版本 `0.2.0`。

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
claude plugin uninstall snowball-companion@snowball-local --scope user --keep-data
claude plugin list
```

先用 `claude plugin list` 核对实际安装范围；project / local 安装应使用对应 `--scope`。先查 `uninstall --help` 是否支持 `--keep-data`，旧版需先备份宿主插件数据。仅在不再需要该市场时另行运行 `claude plugin marketplace remove snowball-local`。

共享的 Snowball Hub、配对凭据、设备与其他软件集成继续保留。删除插件目录前核对现有服务的 `ExecStart`，避免删掉其他客户端仍在使用的共享运行时。插件状态位于用户的 `~/.config/xueqiu/hook-state/claude-code-plugin/`，不放在插件缓存中。

完整功能、前提、状态语义、数据边界与首次设备配置见 [插件说明](plugins/snowball-companion/README.md)。验证边界见 `VALIDATION.json`。ZIP 不包含账号、会话记录、Wi-Fi 密码、设备密钥或签名私钥。
