# 本机 AI 状态汇聚服务

`status_hub.py` 是纯 Python 标准库服务，监听 `127.0.0.1:4190`。它接收适配器的状态元数据，写入原子 JSON 快照，供现有 BLE bridge 读取。它不读取 AI 对话，也不是能够自动观察任意软件的 MCP 服务器。

## 启动与接入

```bash
python3 status_hub.py
# 另一个终端；使用已经完成 USB 绑定的 bridge 环境：
python3 bridge.py watch --source "$HOME/.local/state/xueqiu/hub/snapshot.json"
```

如使用 `connect-bluetooth.sh` 管理后台桥接，请先停止原有进程，避免同时连接同一手表。这个服务本身不操作蓝牙、不安装软件 hooks，也不设置开机自启。默认端口占用会直接报错，不自动改成对外监听地址。

首次启动生成 `~/.config/xueqiu/hub-token`，目录权限 0700、文件 0600。不要复制 token 到项目、示例或发布包中。运行状态位于 `~/.local/state/xueqiu/hub/`，包括 `sessions.json`、`snapshot.json` 和单实例文件锁。这里的文件不属于可分享开发包。Ctrl-C 或 SIGTERM 会把输出快照标记为不可用；如果进程被强制结束，桥接端仍须按时间戳判断过期。

### 事件接口

`POST /v1/events`，`Content-Type: application/json`，`Authorization: Bearer <本机 token>`。最大请求 4096 字节；拒绝额外字段、重复 JSON 键、非法类型、错误枚举和过期消息。API 不开放 CORS，也拒绝含浏览器 Origin 的请求。网页若要接入，应由受控本机后端转发，不能把 token 嵌进前端。

```json
{
  "version": 1,
  "provider": "Codex",
  "source": "codex-hooks",
  "session_id": "sha256-derived-opaque-session-id",
  "event_id": "unique-event-id",
  "seq": 1,
  "timestamp": 1791820800.0,
  "kind": "task",
  "phase": "thinking",
  "ttl_seconds": 30
}
```

示例时间仅说明格式，发送时必须用当前 UTC Unix 秒。字段规范见 `status-schema.json`。允许状态枚举、标识符，以及可选 `display_name` 会话名称（最多 64 Unicode 字符 / 192 UTF-8 字节，无控制字符；空值清除旧名）。可另带用户授权的显式提问标题 questions；禁止发送答案、任意 prompt、命令正文、源码、其他工具输出或凭证。`session_id` 应由适配器哈希得到；源 ID 稳定，序号在每个 `(source, session_id)` 内持久化并严格递增。哈希 ID 仍属于本机运行元数据，不应无意义地上传。

同一 `event_id` + 完全相同事件的重复请求返回 200 / `duplicate:true`，不会刷新心跳或 TTL。相同 event_id 的不同内容、序号倒退、事件时间倒退返回 409。服务最多保留 128 个会话；达到容量时拒绝新增会话而不丢弃重放保护。更换某个来源身份或清理会话必须同时考虑适配器的序号持久化状态，不应在运行中随意删除文件。

`kind:"heartbeat"` 禁止携带 `phase`，只表示适配器报告自己仍可观察来源。**心跳不能证明任务仍在执行，也不能延长旧任务状态的有效期。** 如果适配器能真实重新观察当前任务，应提交新的 `task` 事件；只有一次性的 hook 回调时，不启动伪造心跳的后台循环。

### 三种时间和存活状态

| 字段 | 含义 |
|---|---|
| `task_updated_at`, `phase_at` | 最近任务事件的本机接收时间与来源时间；source heartbeat 不修改 |
| `task_expires_at`, `task_fresh` | 最近任务观测的有效期与是否新鲜 |
| `source_last_seen`, `updated_at` | 最近来源事件/心跳的本机接收时间；updated_at 为兼容别名 |
| `expires_at`, `source_fresh` | 来源报告的有效期与是否新鲜 |
| `generated_at`, `hub_alive` | 本机 hub 最近输出快照的时间和存活标记 |
| `bridge_alive`, `radio_connected` | hub 不推断蓝牙链路，固定 null，必须由桥接与硬件各自实测 |

源事件 TTL 默认 30 秒，允许 5–300 秒，从事件生成时间和接收时间的较早者计时。长工具调用没有回调时，状态会变成未知；不能以静默推断成功、失败或待机。最终状态 `completed` / `failed` 保存在 `last_state`，但它们不会自动让来源永久在线。`completed` 只代表适配器明确定义的一轮结束，不代表整个项目完成。

当来源和任务都新鲜时，快照包含兼容桥接的顶层 `provider` 和 `state`，`status:"known"`。除独立的未解决问题提醒外，任意一项过期时，顶层 **删除 `state`**、设置 `status:"unknown"`，保留 `last_state` 和两个独立 freshness 标记。这个设计也能阻止旧桥接程序续发过期状态。新桥接程序还必须检查时间戳，以防 hub 意外停止后磁盘文件不变。

### 会话选择

- `GET /v1/snapshot`：返回快照和最小会话列表，需 token。
- `GET /v1/health`：只报告 hub 存活，不能证明来源或蓝牙在线，需 token。
- `POST /v1/focus`，正文 `{"source":"codex-hooks","session_id":"..."}`：固定显示一个已知会话。
- `POST /v1/focus`，正文 `{"auto":true}`：恢复显示最近收到任务事件的会话。

固定会话后其他会话的新事件不会抢走表盘；来源心跳也不会抢焦点。默认自动模式优先未解决问题，再按活动状态和任务事件接收时间选中，不代表软件窗口真实焦点；若需跟随窗口，须另接入有权限、明确可用的焦点来源。

## 验证范围

```bash
python3 -m unittest discover -s . -p test_status_hub.py -v
```

测试覆盖鉴权、Host/Origin 限制、字段/体积校验、序号与事件重放、乱序、过期、终态保留、心跳不续任务、重启持久化、焦点选择和并发读取原子快照。HTTP 测试只在临时目录与临时随机本机端口运行，不会安装真实应用 hooks，也不会推送当前手表。

会话名称在私有 `sessions.json` 和当前快照中保存，过期记录仍可保留名称供历史显示。来源 heartbeat 可以更新显示名称，但不修改 `task_updated_at` / `task_expires_at`，更名不能使过期任务重新有效。旧事件不带此字段时保留已有名称，新会话默认空名称。

### 多会话关注（v0.5）

`POST /v1/focus` 新增 `{"auto":true,"provider":"Codex"}`（Claude/Cursor 同理）。
自动跟随只在该软件内选择；优先未解决问题，再选仍有效的 thinking/running/waiting 会话，最后按任务事件时间选最新。
`{"auto":true}` 保留原有全软件自动模式。指定 source + session_id 仅接受尚有效的活动会话。
锁定后，其他会话的事件不会抢走焦点；原会话完成后仍保留焦点直到用户选择其它会话或恢复自动。
快照新增 `focus_provider`，锁定模式/所选软件持久化。读取快照不会额外持久化焦点变化。

手表目录由桥接器从 sessions 构造，每软件最多8条，total 表示全部活动会话数（可能大于显示条数）。
目录使用 source/session_id 的 SHA-256 截断128位作为传输标识，绝不以显示名称匹配，来源不同的同名/同ID不会相互覆盖。
目录的选择确认只有在本机 Hub 返回正确关注身份后才发送到手表。

### 未解决问题提醒（2026-10-14）

`task` 事件可携带 `pending_input`（0–4096 的整数）：表示尚未回答的异步问题项数，正数时 phase 必须为 waiting。它不包含问题或答案正文。只有明确的对应回复、提交失败/取消、会话结束才消除对应问题；普通工具执行、工具提交返回、Stop、整理上下文和不相关用户消息都不会消除。

该计数独立于任务新鲜度持久化。即使 300 秒内没有新 Hook、或 Hub 重启，未解决问题仍显示 `status:"attention"` / `state:"waiting"`；`task_fresh`、`source_fresh` 和原始任务时间仍如实过期，不推断 AI 继续执行。桥接器只在 Hub 每秒正常发布的快照仍处于 30 秒存活窗口时续发这一已知提醒。Hub 停止或快照不再更新时停止续期。普通过期 running/thinking 状态不享受该规则。Heartbeat 不得创建或清除 pending_input。

自动关注优先待答会话，固定关注不被抢占。目录中的活动项也包括待答项，供用户选中查看；它不代表所有这些会话仍在运算。显式 `pending_input:0` 后恢复正常状态和原有任务 TTL。

离线回归：`python3 ../tools/test_pending_questions.py`（从 companion 目录执行）。所有合成事件使用临时目录中的 Hub，不注入运行中的手表。


## 插件启用但状态一直未同步

先分别检查 BLE 链路、Hub ticker 和事件来源更新时间。Hub 在刷新 `generated_at` 不等于宿主仍在发送任务事件；超过 TTL 后停止续送旧状态是正确行为。2026-10-15 本机问题是标准根 manifest 遮住兼容 manifest，导致 Codex 实际加载 0 个 Hooks；与 BLE 配对或设备心跳无关。0.4.1 兼容修复保留标准文件为 `plugin.agent-plugins.json`，使用 `.codex-plugin/plugin.json` 后官方重装，10 条原有 Hooks 在既有信任下恢复，并收到既有聊天的真实新事件。若另一个宿主不热加载，应重载插件后查看真实新事件时间；不能靠手动 running 或延长 TTL 假装修复。


0.12.0授权范围更新：新增用户明确要求的「提问工具的问题标题」与独立问题ID，不含答案、任意工具输出或命令。支持按问题暂缓600秒、忽略和恢复；不执行回答或批准。正常空闲由在线Hub明确报告，运行状态失效仍保留未知。详见随包 ATTENTION-DETAILS.md。
