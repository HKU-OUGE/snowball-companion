# Claude / Cursor 本机状态适配器

实现时间：2026-10-13。当前交付包含可运行的 hook 脚本、只生成待审配置的工具，以及合成回调测试。**尚未安装到真实 Claude / Cursor 配置，也未验证真实宿主的回调。**

## 支持范围

| 软件 / 场景 | 实现方式 | 当前边界 |
|---|---|---|
| Claude Code CLI，本机执行 | `claude_hooks.py` 接收 command hooks | 可映射提交、工具调用、权限询问、回应结束和 API 错误 |
| Claude Desktop 的 **Code** 页，本机会话 | 同一个 Claude Code adapter | 官方 hooks 适用于 Code 会话；仍待本机宿主验证 |
| Claude Desktop 普通 **Chat** / Cowork | 未实现自动观察 | 不把 Code hooks 的支持范围扩大到整个 Desktop |
| Cursor 本机 IDE Agent | `cursor_hooks.py` 接收 command hooks | 提交后处理中；显式结束状态映射本轮完成 / 异常 / 待机 |
| Cursor CLI | 暂不标记为已支持 | 必须按安装版本做实际回调验证，不能从 IDE hooks 推断完全一致 |
| 云端 / SSH / 容器中的会话 | 未配置远程转发 | `127.0.0.1` 指的是执行 hook 的机器；当前设计只连接这台电脑 |

Claude 的 [hooks reference](https://code.claude.com/docs/en/hooks) 定义了事件和 command-hook 输入输出；[Desktop 文档](https://code.claude.com/docs/en/desktop) 区分 Chat、Cowork 和 Code。Cursor 的 [hooks 文档](https://cursor.com/docs/hooks) 定义了事件、配置格式与退出行为。脚本只使用这里明确列出的接口，不读取私有聊天数据库。

## 状态语义

**“本轮完成”不代表整个项目成功。** 不读取回复中的“完成了”等文字推断成功，也不分析工具命令、输出或代码。

Claude 映射：

| 回调 | 表盘状态 |
|---|---|
| SessionStart / SessionEnd | 待机 |
| UserPromptSubmit | 处理中 |
| PreToolUse | 执行中；AskUserQuestion 例外为等待确认 |
| PermissionRequest | 等待确认 |
| PostToolUse / PostToolUseFailure | 处理中，工具失败可能由代理自行恢复 |
| Stop | 本轮完成：主助手本轮响应结束 |
| StopFailure | 异常 |

带 `agent_id` 的子代理事件不覆盖主会话；本版没有聚合所有子代理。PreToolUse 是调用尝试开始的信号，随后若触发 PermissionRequest，界面会进入等待确认。

Stop 对应“本轮响应结束”，会触发完成动画，**不表示整个项目成功、测试通过或后台工作全部结束**。`stop_hook_active` 只表示此前因 Stop hook 继续过，后台任务和定时任务也不否定本轮主回复已经结束，因此都不阻止这次完成反馈。其他 Stop hooks 仍可能要求继续；后续真实提交、工具事件会覆盖 completed。本适配器不阻止停止、不要求继续，也不检查回复正文是否声称成功。参见官方 [Stop、输入与决策规则](https://code.claude.com/docs/en/hooks#stop)。

Cursor 映射：

| 回调 | 表盘状态 |
|---|---|
| sessionStart / sessionEnd | 待机；sessionEnd.reason=error 为异常 |
| beforeSubmitPrompt / postToolUse / postToolUseFailure / preCompact | 处理中 |
| stop.status=completed | 本轮完成 |
| stop.status=error | 异常 |
| stop.status=aborted | 待机 |
| 未识别或缺少显式 status 的 stop | 不更新 |

Cursor 的工具许可 hooks 会参与动作决策，本适配器**不注册** preToolUse、beforeShellExecution、beforeMCPExecution 等回调。因此目前不自动展示 Cursor 的“工具开始执行”和“等待确认”，也不会替用户批准任何工具。afterAgentResponse 只是单条消息结束，本版没有拿它触发成功。

## 数据与心跳

路径：`软件 hook → 本机 status_hub → BLE bridge → 手表`。

hook 只向固定的 `http://127.0.0.1:4190/v1/events` POST，忽略环境代理、拒绝 HTTP 重定向，网络超时 0.35 秒。认证使用 hub 创建的 `~/.config/xueqiu/hub-token`，要求文件属于当前用户且仅该用户可读写。

允许发送的字段为：`version, provider, source, session_id, event_id, seq, timestamp, kind, phase, ttl_seconds`。`session_id` 是加 source 前缀后的 SHA-256 摘要。只有枚举状态穿过发送边界；prompt、命令、工具参数、聊天正文、路径、账号、凭据不会记录或发送。脚本最多读取 64KiB stdin，过大或无效输入会跳过，避免状态收集阻塞宿主。

每个会话的序号存在 `~/.config/xueqiu/hook-state/<source>/<opaque-id>.json`，文件只有 `seq`。锁覆盖序号分配及发送，失败事件不重试，避免旧状态稍后覆盖新状态。目录 0700、文件 0600，不进入开发包。

**hook 不是持续在线的进程，不能用定时器重发旧状态冒充软件仍在工作。** 每个新事件有效期 30 秒，没有新事件时由 hub 标为过期；长工具运行超过这个间隔也可能显示过期，表示缺少新证据，而不是任务失败。BLE 链路心跳由电脑端 bridge 管理，它证明连接存活，不证明 AI 任务状态仍新鲜。后续如需长任务持续在线，要增加有实际宿主存活证据的连接器。

MCP 可以提供“上报状态”工具，但仅部署 MCP 服务器不会自动获得宿主所有任务、权限和结束事件。显式工具调用应标为主动上报来源；本适配器采用自动 hooks，不依赖模型记住每一步调用 MCP。

## 生成配置，保留已有 hooks

先按照电脑端状态中心说明启动 `status_hub.py`。随后在 `desktop` 目录运行：

```sh
# 只生成新文件，不激活；不会创建真实 ~/.claude 或 ~/.cursor 配置。
python3 adapters/claude_hooks.py config --output claude-hooks.preview.json
python3 adapters/cursor_hooks.py config --output cursor-hooks.preview.json

# 已有配置时，生成保留原字段、原 hooks 的合并副本：
python3 adapters/claude_hooks.py config \
  --existing ~/.claude/settings.json --output claude-merged.preview.json
python3 adapters/cursor_hooks.py config \
  --existing ~/.cursor/hooks.json --output cursor-merged.preview.json
```

输出内的 Python 和脚本路径是生成时的绝对路径，因此换位置后需重新生成。生成器不覆盖文件、不更改活跃配置、不安装插件；已有 hook 条目保留，完全相同的新增条目不会重复。待确认接入目标与生成内容后，再将已审查副本配置到目标软件。安装后应使用该软件的 hooks 检查界面确认回调，不应将本项目的合成测试作为实机宿主验证。

异常输入、缺少 token 或 hub 不可达时：Claude 安静退出 0、不输出权限决策；Cursor 只保持原有 prompt 提交路径，其余回调输出空 JSON，不返回工具权限，也不生成后续提示。

## 合成验证

在 `desktop` 目录运行：

```sh
python3 -m unittest discover -s adapters -p 'claude_cursor_hooks_test.py' -v
```

19 项覆盖：状态映射、Stop 仅表示本轮响应结束、已有后台工作时的本轮完成、后续活动覆盖 completed、工具失败与整轮失败区分、子代理过滤、Cursor 许可 hooks 排除、失联不阻塞、输入限制、字段白名单、敏感数据丢弃、序号并发、有故障时不回放旧状态、私有文件校验、配置保留与幂等；还以临时 hub 验证了真实本机 HTTP 接收及错误凭据拒绝。测试仅使用合成回调和临时凭据，没有启动真实 AI 任务，没有发给手表。
