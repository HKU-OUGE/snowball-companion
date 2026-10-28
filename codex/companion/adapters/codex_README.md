# Codex → 雪球：本地生命周期接入

核查日期：2026-10-13。此电脑实测 `codex-cli 0.162.0-alpha.17.2`，可执行文件 `/usr/lib/chatgpt/resources/codex`，`codex features list` 中 `hooks` 为 `stable true`。已在本机通过官方命令安装并启用 `xueqiu-status@xueqiu-local` 0.2.0，10 个 Hook 通过官方审阅界面信任；真实 CLI SessionEnd 已进入 Hub 并获得 BLE 界面应用回执。Desktop 已在重启后发出真实工具生命周期事件，显示名称已通过本机状态服务观测。没有使用信任绕过标志，也没有启动模型任务。独立插件安装包见同级输出 `xueqiu-codex-marketplace`。

## 推荐路径

`Codex 本地生命周期 Hook → 127.0.0.1:4190 状态中心 → 现有 BLE bridge → 手表`

插件负责打包 Hook，不承担蓝牙连接。蓝牙和心跳由电脑端状态中心统一维护。普通 MCP 工具只能处理宿主实际调用它的请求，不会自动获得全部桌面任务状态，因此仅安装 MCP 服务不足以实现自动同步。Codex 也支持由生命周期 Hook 调用 MCP 工具，但对这个本地设备，短时本地命令 Hook 更直接。

依据：[OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks)、[插件中的生命周期 Hooks](https://developers.openai.com/plugins/build/plugins#bundled-mcp-servers-and-lifecycle-hooks)。这些文档覆盖本地 Codex / 本地执行的桌面任务；不要将其推广为所有云端 Work 会话的可用能力。

## 已实现的映射及边界

| 官方事件 | 表盘阶段 | 精确含义 |
|---|---|---|
| `SessionStart` | 待机 | 会话启动或恢复 |
| `UserPromptSubmit` | 处理中 | 用户输入被提交；不是模型内部思考测量 |
| `PreToolUse` | 执行中 | 即将调用本地工具；工具仍可能被其他 Hook 拦截 |
| `PermissionRequest` | 执行中 | 审批流程开始前回调；可能由自动审核或其他策略处理，不能证明已向用户提出请求 |
| `PreToolUse`，工具名为 `request_user_input` / `request_user_input_async` | 等待确认 | 用户输入工具即将调用 |
| `PostToolUse` | 处理中 / 执行中 / 等待确认 | 用匿名工具 ID 维护并发工具集合，尚有工具时保留对应阶段 |
| `Stop` | 本轮结束（协议 `completed`） | 模型停下；**不是项目成功**，其他 Stop Hook 可能触发继续 |
| `Interrupt` | 待机 | 用户中断当前轮次，不显示成功 |
| `SessionEnd` | 待机 | 正常结束事件；不覆盖强制杀进程、断电、崩溃 |
| `PreCompact` / `PostCompact` | 处理中 | 正在整理上下文 |

Hooks 没有本实现可确认的统一 `TurnFailed` / 崩溃事件，因此不会猜测为异常。托管 WebSearch 等特殊工具不一定触发本地工具 Hook。子代理的 Hook 可能使用父会话 ID，此版本是会话级状态近似，不提供每个子代理的独立完整追踪。多个同会话 turn 同时运行时也不提供精确的逐 turn 聚合。

2026-10-16 修正：`PermissionRequest` 不再触发“等你回应”，自动通过、自动拒绝或普通工具报错都不是用户待答证据。已发布的 Hook 输入没有“人工审批弹窗已出现”或审批路由结果字段，因此本适配器暂时不能单独识别原生人工审批弹窗；需要将来接入已确认的 app-server 审批请求与解决事件，不能按超时或错误文字猜测。真正的 `request_user_input` 与 `request_user_input_async` 仍立即触发待答，异步问题持续到对应答复、明确取消/失败或会话结束。并行工作、自动审批和其他会话活动不会清除真实问题。

对明确使用 `codex exec --json` 的 CLI，另外提供 JSONL 模式：`turn.failed` 显示异常，`turn.completed` 显示本轮结束；普通 `error` 或某条命令非零退出不直接等于整轮失败。流关闭也不被假定为成功。依据：[官方 JSONL 事件说明](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable)。此模式只观察接入该管道的新 CLI 运行，不能观察已打开的桌面任务。

## 安装方式：先生成，再由用户审查信任

在 `native-app/desktop` 目录执行：

```bash
python3 adapters/codex_hook.py config > adapters/codex-hooks.generated.json
```

这只生成配置片段。检查生成文件中的脚本绝对路径与内容后，将其中 `hooks` 各事件的处理器合并进 `~/.codex/hooks.json`（全局），或某个项目的 `.codex/hooks.json`（仅该项目）。**已有文件应先备份并合并，不能直接覆盖。** 生成的绝对路径要保持有效；移动开发包后应重新生成。

随后在 Codex CLI 打开 `/hooks`，逐条检查并信任雪球 Hook。官方要求：“Non-managed hooks must be reviewed and trusted before they run.” 定义更改后需要重新审核。此适配器不自动写入信任记录，也不使用跳过信任的标志。配置载入与信任操作完成后，用下一次正常工作观察真实回调；不要把本目录的合成测试当作已完成这一步。

如果以插件分发，可以用已生成的 `codex-plugin/`，或重新导出到一个不存在的新目录：

```bash
python3 adapters/codex_hook.py export-plugin /绝对路径/xueqiu-status-plugin
```

目录内有 `.codex-plugin/plugin.json`、`hooks/hooks.json` 和自包含 `scripts/codex_hook.py`。按桌面端的本地插件导入流程安装，之后仍要完成 Hook 信任；**本机已通过 codex plugin add 正式安装并验证启用状态**。插件可手动分发，带生命周期 Hook 的插件当前不应当作公共插件目录提交包。导出命令不会安装或启用插件。

## 明确选择 CLI 管道时

以下示例会运行真实 Codex 任务，应在用户实际要执行该任务时使用；本次验证没有运行它：

```bash
set -o pipefail
codex exec --json --output-last-message /你选择的私有目录/final.txt '你实际要执行的任务' \
  | python3 adapters/codex_hook.py jsonl
```

JSONL 适配器消费事件但不把 prompt、回答、命令、工具结果写到日志或手表。需要保存最终回答时，由 Codex 的 `--output-last-message` 写到用户选择的私有文件。不要再通过 `tee` 无意保存完整原始事件流。同一次 CLI 工作应选择 Hooks 或 JSONL 一种接入方式，避免在状态中心生成两个来源。

## 更新、心跳和隐私

- Hook 每发生一次就提交一次事件。每个来源/会话的序号在私有状态文件中递增，文件锁避免并发重复；HTTP 超时 250ms，故障返回 `{}` 和退出码 0，不阻止 AI 工作。
- 只发送协议版本、软件名、来源、匿名会话 ID、随机事件 ID、序号、时间戳、阶段、TTL、待答数量和授权的会话显示名称。对 prompt、完整回复、工具参数、源码、目录、模型名和 transcript 路径均不发送、不持久化，也不会打开 transcript。
- 电脑本机 Bearer token 由状态中心生成，读取 `~/.config/xueqiu/hub-token`。请求固定发送到 `127.0.0.1`，禁用 HTTP 代理和重定向，避免 token 被转发。插件内没有密钥。
- `~/.local/state/xueqiu/codex-adapter/` 只保存序号、匿名工具 ID、待答题目索引与内部活动阶段，目录 0700，记录 0600。
- 该适配器**不伪造 AI 心跳**。长工具和长时间推理可能没有新回调；300 秒无事件后来源应标注“更新已过期/未知”，不能据此宣称异常。状态中心/蓝牙的 5 秒心跳只证明连接存活，不能证明 AI 正在执行。状态中心需保留任务事件时间与传输心跳时间的区别。
- 删除 Hook 配置/禁用插件即可停止采集；不会影响独立蓝牙演示模式。私有状态目录可在适配器停止后删除。

## 验证

```bash
python3 -m unittest discover -s adapters -p 'codex_test.py' -v
```

合成测试覆盖映射、并发序号、并行工具、审批流程不冒充人工待答、隐私白名单、错误/离线 fail-open、拒绝重定向、自包含插件导出；插件 `test_pending_questions.py` 还覆盖持续待答、部分答复及真正输入工具的等待。它们不启动模型，不代表真实 Codex Desktop Hook 已经触发。实际验收还需要：完成 `/hooks` 信任，在一轮正常任务中观察提交、工具、结束事件，并在状态中心核对来源时间与 BLE 应用回执。

## 0.2.0：真实会话名称

本次新增授权的 `display_name` 元数据，最多 64 个 Unicode 字符且不超过 192 UTF-8 字节。控制字符会移除。数据库查询超时、记录不存在或 `name` 为 NULL（尚未生成）时，事件省略 `display_name`，Hub 保留同一会话之前的真实名称；如果从未取得名称，界面显示名称未提供。只有成功读取到明确的空字符串名称时才发送 `display_name: ""` 清除旧名称。不会用演示任务填充真实任务名称。

官方 Hooks 的公共字段保证 `session_id`，没有保证标题字段。适配器因此只在本机 `$CODEX_HOME/state_N.sqlite`（默认 `~/.codex`）以只读 SQLite、50 ms 超时、精确主键查询 `threads.name（旧 schema 无 name 时才使用 title） WHERE id = session_id`。最多尝试 3 个已存在的状态数据库；不读取 transcript、first_user_message、preview、工具参数或 prompt。此数据库是本机当前版本观察到的内部结构，并非稳定公开 API；结构变化时省略本次名称字段，保留同一会话已知名称；从未取得名称时保持未命名。标题只进入本机私有状态和已配对手表。

工作区插件包已升为 0.2.0。修改脚本和 Hook 描述后应升级本机安装并重新走 Codex 信任审阅；本节不代表新版本已经安装。BLE v2 需要对应新版固件，详见 `../SESSION-METADATA.md`。

### 异步问题未回答（2026-10-14 修正）

`request_user_input_async` 的 PostToolUse 表示问题提交完毕，并不代表用户已经回答。当前适配器单独保存其匿名调用 ID 与题目索引。Desktop 结构化回复信封通过 UserPromptSubmit 到达时，按 questionItemId 匹配并逐项消除；普通用户消息、并行工具、Stop 和上下文压缩不会误清。只有所有问题解决后，表盘才恢复执行/思考阶段。原始 activity_phase 独立保存在本机。该本机回复信封不是官方独立 Hook 事件，若别的客户端不提供此信封，不能声称自动识别自由文本答案。

Hub 接收 pending_input 数量，并将其视为独立未解决事项。活动时间和 freshness 不续期；持续等待的提醒由活跃 Hub 的快照提供 30 秒传输租期，停掉 Hub 或蓝牙后不会伪造连接。只更新脚本，不改变已信任 Hook 的定义。
