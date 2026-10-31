# Snowball Companion for Claude Code · 0.2.0

让雪球显示 Claude Code 的会话状态。适用于 **Claude Code Desktop 的本地 Code 会话与本机 CLI**，复用现有 Snowball Hub → 蓝牙／Wi-Fi → ESP32 通道。不会烧录固件，也不会改动 Codex 插件或设备上的角色资源。

## 安装

先在配套 marketplace 根目录执行 `claude plugin marketplace add .`，再执行：

```sh
claude plugin install snowball-companion@snowball-local --scope user
```

从配套 marketplace 安装后，在新的本地 Code 会话中运行：

```text
/snowball-companion:setup
/snowball-companion:status
/snowball-companion:pet 雪球
```

插件只依赖 Python 3.10+ 标准库。生命周期 hooks 使用 `python3`，请确保 **Claude Desktop 启动的环境**能找到它；终端能找到并不一定代表 Desktop 环境也能找到。当前验证平台为 Linux；同一 Python/fcntl 实现适用于 macOS，但尚未做 macOS Desktop 实机验证。原生 Windows 不在这一版本的支持范围。

已配置过 Snowball 的电脑无需再配对：运行 setup 检查本机 Hub，并在雪球「设置」中选择 Claude。新打开或继续运行的 Claude 会话会进入会话列表，复用既有优先级排序。插件不会自动抢占用户当前选择的软件。

## 状态规则

| Claude 事件 | 雪球表现 |
| --- | --- |
| SessionStart / SessionEnd | 待机 |
| UserPromptSubmit、工具返回、压缩上下文 | 处理中 |
| PreToolUse | 执行中 |
| AskUserQuestion 开始、且没有现成 answers | 等你回应，并显示问题正文 |
| Notification.permission_prompt | 等待操作权限确认 |
| MCP elicitation 通知 | 等待补充信息 |
| Stop | 本轮完成，不代表整个项目成功 |
| StopFailure | 本轮异常 |

不注册 PermissionRequest：它可能被其他 hook 自动批准，不能证明用户已看到问题。实际 permission_prompt 通知一般在提示框等待约 6 秒后发生，因此这类提醒会有短暂延迟。自动批准、普通工具错误和子代理完成都不会误报为用户待答或主任务完成。

每个会话独立保存序号和未解决的问题。普通工具返回只清除对应的 AskUserQuestion；未回答的问题不会被另一工具事件或 Stop 抹掉。收到对应工具结果、用户提交新消息或结束会话后清除。权限通知没有关联 tool_use_id，后续工具活动会解除该权限提醒。MCP elicitation 的完成／回应通知解除相应提醒。只有通知、没有独立问题 ID 的阻塞提示也会在本轮 Stop 时解除，避免用户拒绝权限后残留等待。

普通状态证据有效期为 **5 分钟**，没有新事件便由 Hub 显示过期／待同步，不会用定时重发冒充 Claude 仍在运行。待答提醒单独保留，用户可在现有网页／设备上稍后处理或忽略。BLE 链路心跳由现有 bridge 负责，与 AI 工作状态是否新鲜分开。

官方 hook 没有保证提供 Desktop 侧栏名称。本版使用 **Claude · 项目目录名 · 会话短编号**区分会话，不读取私有会话数据库或从完整 prompt 推导标题。只显示安装后实际观察到的会话，不枚举从未触发事件的历史侧栏。

## 数据边界

发送到固定的 `http://127.0.0.1:4190/v1/events`：枚举状态、匿名会话 ID、项目显示名、时间戳，以及明确提出的问题正文。问题最多 256 字／768 字节；不发送提示词、工具命令、输出、答案、transcript_path、API key 或权限建议。权限提醒使用固定文案，不转发可能含命令的 notification.message。

hook 静默退出 0，永不返回 allow/deny/updatedInput/decision 等权限决策。Hub 缺失、输入损坏、网络失败或锁繁忙时跳过；网络超时 350ms、锁等待最多 200ms、hook 总超时 2 秒。凭据只读取用户自己的 `~/.config/xueqiu/hub-token`，忽略代理并拒绝重定向；状态目录 0700、文件 0600。失败事件不排队回放。

本版是单向状态／问题提醒，**不能从雪球向 Claude 回答问题、批准权限、发送新任务或语音指令**。普通 Chat、Cowork、SSH、容器和云端运行未接入；那里的 localhost 并非这台电脑。也不承诺 Claude 内置与 ChatGPT Pets 相同的悬浮桌宠接口。角色切换作用于已有 Snowball 设备／网页生态。

## 首次配置一台没有 Snowball 服务的电脑

以下命令均从本插件根目录执行。已有 Hub 时不要再启动第二个。

```sh
python3 scripts/run_hub.py
```

这是只管理 Snowball 事件的前台进程，保持该终端运行。状态服务不会读取其他 AI 应用。首次蓝牙连接需要已有 Snowball 固件的设备及 USB 数据线；在另一个终端建立独立环境，再按实际设备路径配对：

```sh
python3 -m venv ~/.local/share/snowball-companion/venv
~/.local/share/snowball-companion/venv/bin/python -m pip install -r runtime/requirements.txt
~/.local/share/snowball-companion/venv/bin/python runtime/bridge.py pair-usb --port /dev/ttyACM0
~/.local/share/snowball-companion/venv/bin/python runtime/bridge.py watch --source ~/.local/state/xueqiu/hub/snapshot.json
```

`/dev/ttyACM0` 是 Linux 示例，先确认设备；macOS 使用实际 `/dev/cu.*`。配对会生成这台电脑自己的私有配置，不能从别人那里复制。独立设备配对流程与 macOS BLE 没有在本次插件打包中做新实机测试。Wi-Fi、远程中继、开机服务继续由已配置的 Snowball Companion 管理，本插件不自动配置它们。

## 官方接口依据

- [Desktop 安装插件与运行位置](https://code.claude.com/docs/en/desktop#install-plugins)
- [插件 manifest 与路径](https://code.claude.com/docs/en/plugins-reference)
- [Hook 事件与通知语义](https://code.claude.com/docs/en/hooks)
- [Marketplace 与本地安装](https://code.claude.com/docs/en/plugin-marketplaces)

本项目是独立的第三方插件，与 Anthropic 官方插件无隶属关系。
