# Windows 确认发送到 Codex

Windows 状态同步、录音和本地识别仍各自验收。本实现给用户确认后的新指令接入
Codex 已安装应用的原生工具命名管道，调用 `send_message_to_thread` 投递到设备选中的
现有聊天；不运行另一套 CLI、不改模型或权限、不创建替代聊天。现有明确确认的新建聊天
流程也可使用该工具通道。普通异步问题回答通过相同管道投递准确的问题回答 envelope；
发送前再次核对待答问题的聊天、工具调用、序号、问题文字和选项。问题过期则拒绝发送。
应用接收回答不等于桌面问题卡片已自动收起，不为收起卡片而重发回答。

## 前提与发现

- 当前支持 Windows Store 的 `OpenAI.Codex_*__2p2nqsd0c76g0/app/ChatGPT.exe`。
  其他分发位置需另行验证，不能去掉安装路径校验来强行连接。
- v0.5.5 的正式 Hook 和 Setup 会接收宿主注入的 `CODEX_APP_TOOLS_PIPE_PATH`，将端点名称交接到
  当前用户的 `.config/xueqiu/codex-app-endpoint.json`。文件使用经读回验证的用户/SYSTEM ACL，
  不保存其他环境变量、凭据或对话。后台 Hub 可读取这份交接，不要求登录任务继承 Codex 环境。
  仅接受本机 `\\.\pipe\codex-app-tools-<UUID>`，不枚举任意命名管道，不扫描进程环境或凭据。
- 连接后读取服务进程身份，核对当前用户 SID 和受保护的 Store 安装路径，成功后才发送请求。
  健康探测只请求工具目录，要求存在 `codex_app/send_message_to_thread`。
- 应用重启换端点后，新版 Hook 会刷新交接；Hub 发现端点变化后重新探测。旧的继承端点已关闭时，
  可以在尚未发送任何数据的阶段尝试新交接。写入后的失败不切换端点、不重发回答。
  未启动 Codex、未运行新版 Hook 或服务身份不匹配时仍显示不可用。交接文件存在不代表回答接口可用。

## 升级旧后台服务

先核对实际运行目录，确认完整发布包包含 `companion/windows_app_endpoint.py` 和
`companion/answer_diagnostics.py`，不能仅凭目录上的版本号认定代码已经更新。

在 **Windows Codex 当前聊天的工具环境** 中，从完整包的 `codex/` 目录执行：

```powershell
.\install-windows.ps1
python -X utf8 .\companion\answer_diagnostics.py --remember-host
```

此时第一次升级的旧 Hub 仍可能显示不可回答，因为它尚未加载新模块。使用归属已核实的
本安装服务管理器重启雪球服务；它不重启 Codex、不修改配对或聊天：

```powershell
.\companion\windows-services.ps1 -Action Stop
.\companion\windows-services.ps1 -Action Start
python -X utf8 .\companion\answer_diagnostics.py
```

`Start` 需要该安装已有登录任务；尚未安装任务时，通过控制台启用 Windows 登录自启。
使用安装了设备依赖的 Python；若它不在默认环境，改为本安装的 `.venv\Scripts\python.exe`。
普通 PowerShell 没有宿主变量时，`--remember-host` 不会编造端点，应回到 Codex 工具环境执行。

诊断分别报告当前进程的 `app_tools_available` 与后台快照中的 `hub_can_create`、
`hub_instruction_available_count`、`hub_answerable_questions`。同时核对快照年龄；不能把前者为真
当成后台 Hub 已就绪。诊断不输出问题文字、聊天标题、管道名称或密钥，不发送消息。

宿主要求审阅新版 Hooks 时，按正常 Review hooks 流程由用户完成。旧聊天可能继续引用旧版 Hook；
升级后使用新版 Setup 或开启新聊天，保证之后的端点刷新由新版 Hook 接管。

## 投递与去重

设备确认后才调用认证 Hub 的 `/v1/message`，保留原 `session`、`request_id` 和文字。
Hub 根据本机未归档会话目录解析准确目标，持久化请求摘要和状态后才调用应用工具。
相同请求不重放；同 ID 不同正文拒绝。正文不另存投递账本。

协议为小端 4 字节长度前缀加 JSON，支持分段读写、20 秒总 I/O 时限和 8 MiB 回执上限。
写入开始后断线、超时或回执不明，标记 `unconfirmed`；不切换另一条路径重发。
若设备提示送达未确认，先检查电脑聊天与账本，不再次点击发送来碰运气。

## 验收

1. 隔离测试覆盖真实 Windows 管道、中文分段传输、超时取消、不可信服务拒绝和不重复投递。
2. 在实际宿主做只读工具目录与项目查询，证明传输和应用协议可用；不发送测试任务。
3. 用户在设备选中目标、核对识别文字、明确确认发送后，再核对目标聊天收到该文字、
   Hub 对应请求 `accepted`、设备成功回执。前两步不能代替这一步。
4. 问题回答另做真实验收：用户确认后核对对应聊天收到准确问题的回答、Hub `accepted` 和设备回执。
   命名管道单元测试不等于该实测完成。分别记录未验证的分发方式和应用重启后重连。

完整包代码：`codex/companion/windows_app_pipe.py`、`app_tools.py`、`codex_desktop.py`。
本地源码/测试通过不代表已推送、发布安装包或完成真实设备验收。
