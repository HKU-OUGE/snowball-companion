# 将 AI 软件状态同步到雪球手表

本文主体保留 2026-10-13 的首次接入说明；新增的 Linux 登录恢复方式见 [电脑端指南](README.md#登录后自动恢复linux0160)。当前本机 Codex 已有真实回调及 BLE 回执验收；下表其他宿主的测试边界仍适用。

首次交付说明：当前交付已实现本机状态中心、BLE 桥接的新鲜度检查、Codex 插件/Hook 适配器、Claude Code 与 Cursor 的配置生成器。**这些软件适配器通过了合成事件测试；真实宿主的安装、信任和回调验收仍需要完成，不能把测试结果当作已接入所有桌面软件。** 本指南中的安装、启动与配置命令供实际启用时使用，编写指南本身没有执行它们，也没有更改用户已有软件配置。

## 数据如何到达手表

```text
Codex 插件中的生命周期 Hooks ─┐
Claude Code 的 command hooks ├→ 本机适配器：只提取阶段和匿名标识
Cursor IDE Agent hooks ──────┘       ↓ 带本机 token 的 HTTP 事件
                              127.0.0.1:4190 状态中心
                                      ↓ 原子 JSON 快照
                                电脑端 BLE bridge
                                      ↓ 签名状态包 + 应用回执
                                  雪球原生圆屏 APP
```

插件负责打包脚本与 Hook 配置；状态中心负责多会话、去重、顺序、焦点和有效期；BLE bridge 负责发现已绑定的手表、认证、传输和重连。三者可以分别维护，不需要每增加一种 AI 软件就修改手表固件。

普通 MCP 服务器只有在宿主实际调用工具时才收到请求，**仅安装一个 MCP 服务不会自动观察宿主全部任务、权限和结束事件**。如果以后增加 `report_status` 一类 MCP 工具，应把它视为显式上报来源；不能假定模型每一步都会记得调用。目前自动同步采用官方生命周期 Hooks。Codex 支持将 Hooks 随插件打包，具体生命周期及信任规则见 [OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks) 和 [插件打包文档](https://developers.openai.com/plugins/build/plugins#bundled-mcp-servers-and-lifecycle-hooks)。

## 软件支持矩阵

“已实现”表示代码和合成测试覆盖该入口，不表示已经在用户真实会话中收到回调。

| 软件入口 | 本次实现 | 当前可表达的状态 | 主要边界 |
|---|---|---|---|
| Codex CLI，本地会话 | 生命周期 Hook / 插件 | 待机、处理中、执行中、等待确认、本轮结束 | 没有统一可靠的崩溃 Hook，不能推断异常；仍需 `/hooks` 审查信任 |
| Codex Desktop，本地执行的任务 | 同一套 Codex Hook / 插件 | 同上 | 待实际安装与回调验收；不宣称云端 Work 同样可用 |
| 明确接入 `codex exec --json` 的 CLI 任务 | JSONL 适配器 | 包括显式 `turn.failed` → 异常 | 只观察该管道里的新运行；不会观察已经打开的桌面任务 |
| Claude Code CLI | command hooks | 六种状态 | 主助手会话级；子代理事件不覆盖主会话，未完整聚合并发子代理 |
| Claude Desktop 的本地 **Code** 会话 | 同一 Claude Code 适配器 | 六种状态 | 待真实 Desktop Code 回调验收 |
| Claude Desktop 普通 **Chat** / Cowork | 未接入 | 无自动状态 | 不从 Code hooks 推导整个 Desktop 的支持 |
| Cursor IDE 本机 Agent | command hooks | 待机、处理中、本轮结束、显式异常 | 当前不注册工具许可 Hooks，因此不能自动显示执行中和等待确认 |
| Cursor CLI / 云端 / SSH / 容器 | 未标记为已接入 | 需逐入口确认 | Hook 内的 `127.0.0.1` 指执行 Hook 的机器，并非必然是手表所在电脑 |

Claude 的事件格式来自 [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)，Desktop 入口应按 [Claude Desktop 文档](https://code.claude.com/docs/en/desktop) 区分。Cursor 的接入以 [Cursor hooks 文档](https://cursor.com/docs/hooks) 为准；本实现有意避开会参与工具许可决定的回调。

`completed` 的统一显示含义是 **本轮结束**，不是项目成功、测试全通过或所有后台任务都已结束。Codex / Claude 的 Stop 表示本轮主回复停止，其他 Hook 仍可能要求继续；后续真实活动会覆盖它。Cursor 只有显式 `stop.status=completed` 才显示本轮结束。工具失败后可能自动恢复，因此单个工具失败不直接等于整轮异常。

## 1. 固定开发包位置，启动本机状态中心

以下命令都在解压后的 `native-app/desktop` 目录执行。在原始工作目录中，该路径为 `outputs/xueqiu-smartring-native/desktop`。先把开发包放到准备长期使用的位置；生成配置中的脚本路径为绝对路径，移动后需要重新生成。

```bash
# 终端 A：前台运行；Ctrl-C 停止。
python3 status_hub.py
```

也可选择后台管理方式，与前台方式二选一：

```bash
python3 manage_hub.py start
python3 manage_hub.py status
# 停止本管理器启动的 Hub，不操作蓝牙桥接或其他程序：
python3 manage_hub.py stop
```

后台启动会等待携带本机 token 的 HTTP health 检查真正成功，再报告已启动；已有健康进程时重复 start 不会启动第二份。`status` 区分已停止、运行且健康、进程存在但 HTTP 不健康；它不代表 AI 来源或 BLE 在线。端口被其他进程占用时拒绝启动，不抢占端口。停止前同时核对 PID、Linux 进程启动时间与本项目脚本绝对路径，只有匹配的托管进程才会收到 SIGTERM。管理记录与日志分别为 `~/.local/state/xueqiu/hub-runtime.json`、`hub.log`，均在私有目录中；没有配置开机自启。

后台 Hub 与 BLE bridge 仍是独立进程：先 `python3 manage_hub.py start`，再执行下一节的桥接命令。停止时分别使用各自 stop；前台启动的 Hub 仍需回到原终端 Ctrl-C，后台管理器不会接管它。

状态中心只用 Python 标准库，固定监听 `127.0.0.1:4190`。首次启动自动生成私有文件：

| 路径 | 用途 |
|---|---|
| `~/.config/xueqiu/hub-token` | 适配器访问本机状态中心的 token，0600 |
| `~/.local/state/xueqiu/hub/snapshot.json` | 供 BLE bridge 读取的当前快照，0600 |
| `~/.local/state/xueqiu/hub/sessions.json` | 最小会话记录、顺序与重放保护，0600 |
| `~/.config/xueqiu/ble-pairing.json` | 之前经 USB 核对的手表身份与 BLE 签名密钥，与 hub-token 不同 |

目录为 0700。这些文件不属于共享开发包。状态中心不会自动添加开机自启，不会打开公网端口，也不会读取聊天数据库。端口已占用时会报错，不会悄悄改端口或改成公网监听。

## 2. 让 BLE 桥接读取状态中心

手表已经完成 USB 绑定时，不需要再次配对。若此前后台桥接还在发送手动演示状态，先停止它，再明确选择新快照：

```bash
# 终端 B
./connect-bluetooth.sh stop
./connect-bluetooth.sh start --source "$HOME/.local/state/xueqiu/hub/snapshot.json"
./connect-bluetooth.sh status
```

脚本有 `uv` 时会在用户缓存目录创建桥接虚拟环境并安装固定依赖；没有 `uv` 时使用当前 Python，需要预先具备 `requirements.txt` 中的依赖。`status` 报告的是本机桥接进程及来源路径，不等于 AI 软件已经产生事件，也不等于手表无线连接已经验证。不要同时再开一个 `control` / `watch` 客户端争用同一手表。

没有新鲜 AI 事件时，手表显示待同步或更新过期是正常结果。不要通过定时写入假的 `running` 来掩盖来源尚未接入。

## 3. Codex：生成 Hook 配置或导出插件

选一种分发方式即可，避免对同一会话重复上报。

### 直接 Hook 配置

```bash
# 只生成待审文件，不写 ~/.codex/hooks.json。
# noclobber 防止覆盖同名预览文件；已存在时换一个新文件名。
(set -o noclobber; python3 adapters/codex_hook.py config > codex-hooks.preview.json)
```

检查新文件中的脚本路径和事件列表。全局位置为 `~/.codex/hooks.json`，也可选择项目内的 `.codex/hooks.json`；已有文件须先保留副本，再将新的事件处理器合并进去，不能直接覆盖原有 Hooks。选择全局或项目配置决定采集范围。

**Codex 的 Hook 审查与信任必须由用户在宿主里完成。** 官方规则是：“Non-managed hooks must be reviewed and trusted before they run.” 在 Codex CLI 的 `/hooks` 中核对雪球脚本并信任；定义变化后需要重新审查。本项目不写入信任记录、不使用绕过标志。完成配置载入和信任后，再在下一轮正常工作中验收真实事件。[OpenAI Hook 信任说明](https://learn.chatgpt.com/docs/hooks)

### 插件包

已附带 `adapters/codex-plugin/`，也可以导出一个新的独立目录：

```bash
# 目标目录必须不存在；仅导出，不安装。
python3 adapters/codex_hook.py export-plugin ./xueqiu-status-plugin.preview
```

生成 `.codex-plugin/plugin.json`、`hooks/hooks.json` 和自包含脚本。按当前 Codex Desktop 的本地插件导入流程选择该目录，并完成宿主的 Hook 审查信任。**插件导入流程尚未在本机实际验收**，导出成功不代表插件已启用。

### 可选：明确选择一个新的 CLI 运行

```bash
# 此例会真正执行所填写的 AI 任务；只在确实要运行任务时使用。
set -o pipefail
codex exec --json --output-last-message /你选择的私有目录/final.txt '你实际要执行的任务' \
  | python3 adapters/codex_hook.py jsonl
```

这一管道不能订阅已有 Desktop 任务。同一次 CLI 工作请选择 Hook 或 JSONL 一种来源。不要用 `tee` 无意保存完整原始事件流；适配器本身只输出允许的状态元数据。详见 [Codex 适配器说明](adapters/codex_README.md)。

## 4. Claude Code / Cursor：生成保留现有配置的副本

```bash
# 没有现有配置时，只生成新的待审文件。
python3 adapters/claude_hooks.py config --output claude-hooks.preview.json
python3 adapters/cursor_hooks.py config --output cursor-hooks.preview.json
```

如已有配置，改用合并生成；`--existing` 指向实际要接入的配置：

```bash
python3 adapters/claude_hooks.py config \
  --existing "$HOME/.claude/settings.json" --output claude-merged.preview.json
python3 adapters/cursor_hooks.py config \
  --existing "$HOME/.cursor/hooks.json" --output cursor-merged.preview.json
```

输出保留已有字段和已有 Hook 条目，生成器不覆盖活跃配置，也拒绝覆盖同名输出文件。审查内容、保留原文件副本后，再将合并内容配置到选定软件；如果只希望某个项目上报，应选择对应项目配置范围。按宿主要求重新加载并检查 Hook 是否启用，再用正常任务验收。

Claude 脚本不输出工具批准决定；Cursor 当前不注册工具许可 Hooks，也不会替用户批准工具。状态中心离线、token 缺失或输入错误时，适配器放行正常工作流程，不靠失败来阻断 AI。详见 [Claude / Cursor 适配器说明](adapters/claude-cursor-README.md)。

## 5. 更新、心跳和“仍在线”分别是什么

| 层级 | 更新方式 | 当前时间参数 | 能说明什么 |
|---|---|---|---|
| AI 来源与任务观测 | 只有真实 Hook / JSONL 事件才上报 | Codex 事件 TTL 300 秒；Claude / Cursor 30 秒 | 最近确实观察到了这个阶段；静默不证明成功、失败或停止 |
| 本机状态中心 | 事件到达立即输出；每秒更新快照有效期状态 | `generated_at`、`task_expires_at`、`expires_at` 独立记录 | hub 活着不等于来源还有新任务证据 |
| BLE 桥接与设备 | 新状态立即发；有效状态每 5 秒传输刷新；过期时每 5 秒只读认证探测 | 手表超过 15 秒没有有效状态写入显示更新过期 | 只读探测可证明 BLE 链路仍连着，不能续期 AI 状态 |

`task_updated_at` / `task_fresh` 描述任务观测；`source_last_seen` / `source_fresh` 描述来源报告。即便未来有持续运行的来源连接器发送 heartbeat，这种心跳也不会改任务阶段或延长任务 TTL；只有重新观察到任务的 `task` 事件才能更新它。当前几个 Hook 适配器都不发送伪造的周期心跳。

例：Claude 进入一个耗时 90 秒的工具，但中途没有回调。30 秒后任务观测变为未知，桥接停止写入；设备在最后一次写入约 15 秒后提示过期。蓝牙仍可能保持连接。直到真实新事件到达，才恢复有效状态。这是信息不足，并不说明工具失败。

终态也保留自己的来源有效期：快照会保留 `last_state=completed/failed` 作为历史，但不会因此把来源永远标为在线。事件丢失、宿主被强制结束、长时间无回调都进入未知，不由连接心跳补写假的待机或成功。

## 6. 多会话、隐私与故障定位

状态中心保存来源、匿名会话 ID、序号、阶段和时间戳；默认选择最近收到任务事件的会话，可通过带 token 的 `POST /v1/focus` 固定一个已知会话。自动选择不是读取操作系统窗口焦点。完整 API 和认证调用约束见 [STATUS-HUB.md](STATUS-HUB.md)。

目前 BLE 协议只传软件枚举和六种阶段，**不传任务标题、会话正文或完整会话列表**。Hub 的多会话管理不代表手表已经拿到每个真实任务的详情；切换软件的 UI 与选择具体来源会话属于不同层。后续若扩展协议，应继续保留明确的信息边界。

适配器对可能包含正文的 Hook 输入做本机白名单提取，不打开 transcript；发送时禁用 HTTP 代理和重定向。原始 prompt、回复、命令、源码、工具结果、目录路径、模型名称和凭证不发给 Hub 或手表。Hub token 和 BLE 配对密钥也不能放入网页前端或分享包。

排查顺序：

1. 检查宿主 Hook 是否加载、是否信任；只导出插件或生成预览文件不会启用采集。
2. 检查本机 Hub 是否监听 4190，是否产生 `snapshot.json`，来源是否确实为目标软件。不要打印 token。
3. 检查快照 `status`、`task_fresh`、`source_fresh` 及过期时间；`unknown` 表示没有足够的新证据。
4. 用 `./connect-bluetooth.sh status` 确认后台桥接正在读取 Hub 快照，而不是默认的 `desktop-state.json` 手动演示源。
5. 最后核对 BLE 连接与设备应用回执；“进程 Running”不能单独证明手表已应用。

停止时先禁用或移除软件里的雪球 Hook / 插件，再运行 `./connect-bluetooth.sh stop`，最后停止 Hub：前台方式在其终端 Ctrl-C，后台方式运行 `python3 manage_hub.py stop`。原有手动演示仍可独立使用，但不应冒充自动 AI 来源。

## 验证命令与当前边界

```bash
python3 -m unittest discover -s . -p test_status_hub.py -v
python3 -m unittest discover -s . -p test_bridge_source.py -v
python3 -m unittest discover -s adapters -p codex_test.py -v
python3 -m unittest discover -s adapters -p claude_cursor_hooks_test.py -v
```

这些指定测试使用合成事件、临时文件/凭据或模拟链路，不启动付费模型任务。不要宽泛运行所有硬件测试来代替上述隔离验证。真实宿主验收还需要完成配置与信任，在一轮用户本来要进行的工作中核对提交、工具、等待、结束事件，再结合手表回执确认数据确实来自该宿主。


0.13.0 用户授权更新：支持本机 Codex Desktop 普通异步问题的明确选项回答，网页还可输入文本；新增助手已公开的进度/结果摘要。旧文中“仅状态、不发送答案”指历史版本。提醒操作仍不等于回答；新回答接口须用户确认，不处理权限批准。详见 `ATTENTION-DETAILS.md`，私有 IPC 兼容性和实际验证边界分别记录。
