# 安装、升级与卸载

本文适用于本仓库的 Codex、Claude Code 和 Cursor 适配器。命令以 Linux 为例；
先在当前机器查看 CLI 的 `--help`。Claude 和 Cursor 的 macOS 设备联动、原生
Windows 请使用下方专门流程，不要套用 Linux 命令。最后核对日期：2026-11-03。

## 先分清三层

| 层 | 用途 | 卸载一个插件时的默认处理 |
| --- | --- | --- |
| 软件适配器 | 接收 Codex / Claude / Cursor 的状态事件 | 只移除用户指定的适配器 |
| 共享伴随服务 | Hub、蓝牙桥接、Wi-Fi 网关、云端连接 | 保留，其他客户端可能还在使用 |
| 个人数据与硬件 | 配对、密钥、Pet 库、会话设置、固件、SD 资源 | 保留，不随插件卸载擦除 |

安装插件与启用账号里的 ChatGPT Pet 也是两个动作。只有 Codex 版提供现有的
Pet Setup 流程；Claude / Cursor 主要同步状态，不能把它们安装成功写成桌宠已创建。

## 设备配置入口

分发包 v0.5.4 随包提供[雪球控制台](CONSOLE.md)。Codex 包位于 `codex/`，Claude / Cursor 包位于 `console/`；Windows 双击 `Open-Snowball.cmd`，Linux/macOS 运行 `./open-console.sh`。Codex Windows 安装器会创建桌面入口，插件 Setup 也可以打开已登记的控制台。先完成依赖准备，再通过 USB 绑定、蓝牙连接和 Wi-Fi 配网。

控制台本身支持 Windows；Claude / Cursor 适配器的原生 Windows Hooks 仍保持各自已有的支持边界。普通适配器卸载保留共享控制台及服务；完整移除时才核对本安装的快捷方式、登录任务和可选防火墙规则。

## 安装前检查

将仓库放在固定位置，确认 `codex/`、`claude/`、`cursor/` 三个目录齐全。
单独解压安装包时，先进入最外层 `Snowball-Codex`、`Snowball-Codex-Core`、
`Snowball-Claude` 或 `Snowball-Cursor` 目录，再按下面的 `cd codex` / `cd claude`
进入对应子目录；Cursor 的安装命令也从这个最外层目录执行。

```sh
python3 --version
```

若已有服务，先查它们的实际运行目录；不要再启动一套 Hub，也不要用旧安装包覆盖
较新的共享运行时。Linux 上可只读查看：

```sh
systemctl --user list-unit-files 'snowball-*.service'
systemctl --user show snowball-hub.service snowball-bridge.service \
  snowball-network.service snowball-cloud.service \
  -p FragmentPath -p ExecStart -p ActiveState
```

这些服务不一定都已安装。没有 systemd 的环境，应检查已有的前台进程或系统启动项；
不要据此新建同名服务。检查配置时只报告路径和状态，不输出 token、配对密钥或 Wi-Fi 密码。

## Codex Desktop / CLI

稳定 ID 是 `xueqiu-status@xueqiu-local`，显示名称是 **Snowball Companion**。

```sh
codex plugin list --json
codex plugin marketplace list --json
cd codex
./install.sh
```

`install.sh` 检查 Codex CLI，并通过官方 marketplace / plugin 命令安装。它会备份
旧 Hooks，补回升级时被移除但旧聊天仍引用的文件，避免已出现过的
`codex_hook.py: No such file or directory`。不要手动清空插件缓存来升级。

安装后再次运行 `codex plugin list --json`，核对精确 ID、启用状态和实际安装路径。
需要桌宠时运行插件的 Setup / 设置动作，或说“设置 Snowball Companion 并启用雪球”。
如果宿主提示连接 Pets 服务或审阅 Hooks，按宿主流程完成；安装器不会自动修改信任。
新开会话验证加载；需要重载时先等正在运行的任务结束。

CLI 不在 PATH 时，本机某些 Linux 打包提供 `/usr/lib/chatgpt/resources/codex`，
安装脚本会检查这个候选位置；其他机器应使用自己安装的 CLI 路径，不要照抄开发者路径。

### 只卸载 Codex 插件

在宿主插件管理页选择卸载，或确认本机支持后执行：

```sh
codex plugin remove xueqiu-status@xueqiu-local --json
codex plugin list --json
```

Codex 内的代理若有宿主卸载工具，优先使用它；用户已明确要求卸载就不再重复询问。
移除后核对注册列表，并在新会话确认 Hooks 不再加载。旧会话可能仍保留 Hooks 引用，
先结束或重载受影响会话，不要反复删缓存、删除别的插件或重新安装来“完成卸载”。

只有用户也要移除市场来源，且该市场没有其他要保留的插件时，再执行：

```sh
codex plugin marketplace remove xueqiu-local --json
```

已创建的账号 Pet、其他 MCP 连接、设备配对和共享后台服务不由这个命令一并清理。


## Windows 原生安装与自动重连（2026-11-03）

Codex 请使用 0.5.3 或更新版本；并发快照修复、原生测试和支持边界见 [Windows 检查](../codex/docs/WINDOWS-QA-2026-11-03.md)。Claude／Cursor 独立包目前不支持原生 Windows。

Windows 的文件权限、安全缓存读取和服务入口约束见
[Windows 文件与服务兼容说明](../codex/plugins/xueqiu-status/skills/setup/references/windows-compatibility.md)。
当前源码已使用原生 ACL 并读回验证，旧 Linux 服务入口会提前拒绝 Windows；
命名管道失效时不会再尝试 Unix IPC。源码修复不代表已发布新版安装包或完成固件安装验收。

**桌宠安装与硬件连接是两条流程。** 仅安装插件、启用账号 Pet 或在 Windows
蓝牙设置中看到“已配对”，都不会启动 Hub、桥接或登录自启。

**语音是额外的配置与验收项。** 普通 Windows 安装器不下载识别模型。需要录音时，
按[语音初始化与排错](../codex/plugins/xueqiu-status/skills/setup/references/speech-setup.md)
补装本地服务，要求认证健康检查 `ready=true`，再进行设备试录。Windows 消息和建聊
使用[当前宿主的应用命名管道](../codex/plugins/xueqiu-status/skills/setup/references/windows-send.md)，
v0.5.5 通过新版 Hook/Setup 将宿主端点交接给当前用户的后台 Hub，并继续核验服务身份与工具目录；普通异步问题回答带准确问题标识走同一通道。
录音服务就绪不代表任务可送达。

固定保存整个仓库或安装包，不要运行 Unix `install.sh`。PowerShell 中从 `codex/` 执行：

```powershell
.\install-windows.ps1
```

脚本通过当前 Codex CLI 注册本地市场，安装同一个稳定 ID，并仅将已安装插件的
Hooks 改为本机 Python 绝对路径与 UTF-8 模式。它不修改 Hook 信任或账号 Pet；
更新后需按宿主提示审阅变化的 Hooks，再开新聊天运行 Setup。保留旧会话引用的
旧版本脚本；Windows 不使用 Linux 的 `/proc`、systemd 或 shell 安装器。

第一次连接请按[Windows 逐步引导](../codex/plugins/xueqiu-status/skills/setup/references/windows-first-run.md)
提前准备依赖，再提醒用户连接 USB 数据线、核对串口，并在设备「连接电脑」界面启用
**「开始配对」**。环境已准备好的快捷命令为：

```powershell
# COMx 是占位符，请用设备管理器核对后的实际串口替换。
.\install-windows.ps1 -Device -Port COMx -AutoReconnect
```

脚本在 `codex/.venv/` 安装固定依赖；首次下载可能超过两分钟，因此逐步引导会先准备环境，
等用户启用「开始配对」后直接执行 USB 绑定。普通重装遇到已有有效配对时复用；
用户要求从零测试整个设备初始化时，按下方完整重置流程先清除旧连接状态。
新绑定通过 USB 的 DTR 与 CRLF 命令读取设备密钥，凭据只存用户私有目录，禁止打印
密钥或提交文件。`XQPAIR_ERROR open_connection_page` 表示连接页面/两分钟配对窗口已关闭，
应提示用户重新启用**「开始配对」**，收到回复后在窗口内重试；不需要擦除固件。

自动重连使用当前用户、普通权限的 Windows 计划任务
`Snowball Companion Auto Reconnect`，在**登录后**运行，无需管理员或保存密码。
后台监督器维护真实 Hub 和桥接；桥接断线后按 2～20 秒退避重试，进程退出后由监督器
恢复，监督器异常退出由任务计划程序重启。它不会注入演示状态。睡眠/关机期间不能同步。

```powershell
.\companion\windows-services.ps1 -Action Status
.\companion\windows-services.ps1 -Action Stop
.\companion\windows-services.ps1 -Action Start
# 仅在用户要求移除本安装的自启与后台服务时：
.\companion\windows-services.ps1 -Action Remove
```

管理器核对任务归属；同名但属于另一安装的任务会拒绝接管。Stop/Remove 只停止当前用户
且参数包含本安装精确脚本路径的进程，保留配对和账号 Pet。默认 Python 位于
`codex/.venv/Scripts/pythonw.exe`；使用其他已安装环境时，Install 可显式传 `-Python`。
日志在 `~/.local/state/xueqiu/windows/`，凭据在 `~/.config/xueqiu/`，不要共享这些运行数据。

Windows 桥接优先按**精确名称且唯一匹配**查询已配对 BLE 设备，不要求重新公开广播；
候选设备仍必须通过应用 HMAC 身份验证。GATT 服务采用新鲜发现，避免旧缓存导致特征缺失。
先分别确认插件注册、真实 Hook 事件、Hub 健康、设备认证与实际回执，不把任务“Running”
或系统“已配对”当作已同步。Windows 确认后的指令/建聊可使用经过验证的应用命名管道；
异步问题回答也走应用命名管道，接收回执不等于桌面问题卡片收起。
独立空闲证明仍未接通，不能作为固件自动安装的可靠空闲依据。
当前登录任务不会自动发现应用重启后的新端点，需在当前宿主环境重新启动 Hub 并验收。

验证边界：2026-11-02 已在一台 Windows 电脑完成 USB 绑定、BLE 认证、真实设备状态写入
与遥测。用户审阅修正后的 Hook 命令后，当前真实聊天产生适配器记录，Hub 返回 `accepted`，
BLE 设备确认应用了 `running` 和 `thinking` 状态。已验证登录任务注册与停止/启动后台服务
后的自动重连。该结果仅覆盖本机，尚未重启电脑做冷启动验收。代码更新不代表新 Release
或安装包已发布。

## Claude Code Desktop / CLI

在已安装 Claude Code 的机器中，检查实际安装范围后执行：

```sh
claude plugin list
cd claude
claude plugin marketplace add .
claude plugin install snowball-companion@snowball-local --scope user
```

新开本地 Code 会话，运行 `/snowball-companion:setup`。需要升级时先查看
`claude plugin update --help`，使用宿主更新功能更新同一个插件和安装范围，
不要同时再用 `--plugin-dir` 加载另一份副本。

只卸载本例的用户级安装：

```sh
claude plugin uninstall snowball-companion@snowball-local --scope user --keep-data
claude plugin list
```

若实际安装在 project / local 范围，应使用对应范围；CLI 不同版本的参数以 `--help`
为准。`--keep-data` 用于保留 Claude 管理的插件数据目录；旧版本若不支持，先备份该目录或使用宿主提供的保留数据选项。宿主管理的插件选项／授权仍可能随卸载清除，这与外部 `~/.config/xueqiu/` 配对状态不同。仅在该市场也不再需要时运行 `claude plugin marketplace remove snowball-local`。
普通 Claude 聊天产品和 Claude Code 插件不是同一个安装目标。

## Cursor

本仓库提供原生本地插件，不是 VS Code 扩展，不使用 `cursor --install-extension`。
从仓库根目录安装；目标已存在时先核对、备份旧版，不合并覆盖：

```sh
mkdir -p "$HOME/.cursor/plugins/local"
if [ -e "$HOME/.cursor/plugins/local/snowball-companion" ]; then
  printf '%s\n' '已存在安装，请先核对并备份旧版。'
else
  cp -a cursor/snowball-companion "$HOME/.cursor/plugins/local/snowball-companion"
fi
python3 "$HOME/.cursor/plugins/local/snowball-companion/scripts/control.py" doctor
```

在没有活动任务时重载窗口或重启 Cursor，在 Customize / 插件页核对加载。
`doctor` 证明的是 Hub 可达，不能代替真实 Hook 和设备验证。

升级时将旧目录移出 `plugins/local`，再复制完整新目录。卸载手动复制的本地插件时，
同样在退出 Cursor 后将**这个精确目录**移出本地插件扫描目录，或删除已核对归属的
该目录；如果宿主为此安装提供卸载操作，可优先使用。

移动或删除前检查共享服务的 `ExecStart`：如果服务仍从该目录的 `runtime/` 运行，
先将运行时迁到固定的独立目录并验证服务，或在用户要求停止全部 Snowball 服务时
一并停止。不要让其他客户端的后台服务引用一个已被删除的路径。

## 可选设备连接与验证

已经有 Hub / bridge 时直接复用，不重新配对。没有服务的新安装才按相应平台
README 启动随包 Hub 和桥接程序。配对前核对实际 USB 串口与设备型号；安装插件
本身不要求烧录固件、写 eFuse、修改网络或上传设备凭据。

验证按四层分别报告：

1. 插件被宿主注册并加载。
2. 用户实际使用 Agent 后，插件收到真实事件。
3. Hub 收到对应会话的状态。
4. 设备在线并显示相应变化。

隔离测试、手工注入状态和 `doctor` 都不能替代第 2、4 步。不要未经授权代用户发起聊天。

## 停用全部后台服务与彻底清理

用户明确要求从零重走整个设备安装流程时，需包含本安装的后台服务、登录自启、系统蓝牙
绑定、应用配对和旧会话状态；只移除 Pet 和插件不能验收首次连接。
Windows 的归属检查、取消绑定、精确清理范围和独立读回见
[完整重置步骤](../codex/plugins/xueqiu-status/skills/setup/references/windows-first-run.md#为首次连接测试完整重置)。
普通“卸载某个平台插件”的默认保留行为与完整重置分开报告。全新电脑/新设备的首次发现、
依赖安装与权限仍需对应环境实测，不能仅凭缓存重连通过就声称兼容。

只有用户明确要求停止整个 Snowball Companion，而非仅卸载某个平台插件时，才操作
共享服务。先核对归属，再对**实际存在且属于本安装的单元**执行：

```sh
# 下列四个名称均存在且归属正确时适用；缺少的单元应从命令中去掉。
systemctl --user disable --now snowball-cloud.service snowball-network.service \
  snowball-bridge.service snowball-hub.service
```

随后核对 inactive / disabled。如果还要删除服务定义，按刚查到的 `FragmentPath`
逐个删除本安装拥有的单元，再运行 `systemctl --user daemon-reload`。非 systemd
前台进程应通过其原管理器停止，不使用宽泛的 `pkill python`。

普通卸载默认保留以下位置；环境变量或安装方式可能改变实际路径：

| 数据 | 常见位置 | 处理原则 |
| --- | --- | --- |
| Hub token、设备配对、网络凭据、发布密钥 | `~/.config/xueqiu/` | 不整目录删除；没有明确数据清除要求就保留 |
| 会话、设备日志、升级备份 | `~/.local/state/xueqiu/` | 与软件包分开处理，备份按用户选择保留 |
| 独立共享运行时 | `~/.local/share/xueqiu/`、`~/.local/share/snowball-companion/` | 先检查所有服务和其他客户端依赖 |
| 已下载的 OTA 缓存 | `~/.cache/xueqiu/ota/` | 无传输占用时可单独清理；不是签名私钥 |
| Codex Pet 安装收据 | 接收者的 `CODEX_HOME` 私有状态目录 | 收据不等于账号 Pet；删除 Pet 必须通过 Pets 服务 |
| 设备固件、eFuse、SD 资源 | ESP32 / SD 卡 | 电脑卸载不会恢复原厂，也不应顺带擦除 |

若用户要求“彻底删除所有数据”，先列出已发现的精确路径与影响，区分本地凭据删除和
服务端撤销。已明确授权的范围可以执行；未提及的云端部署、其他账号 Pet 或设备擦除
不自动包含在内。不要将密钥内容打印出来用于“证明已备份”。

## 常见问题

- **已安装但没状态**：按插件加载 → Hook → Hub → bridge / 网关 → 设备逐层检查。
- **卸载后服务仍运行**：适配器与共享服务独立，先确定是否也要停用整个产品。
- **重装后想保留配对**：保留私有数据，复用服务；不要重置设备或删除整个配置目录。
- **找不到旧 Hook 脚本**：升级使用随包安装器；卸载后旧会话引用应通过结束／重载解决。
- **需要新系统或全新机器支持**：先确认平台和现有服务。Linux 的本机验证不代表其他系统已验证。

官方命令与宿主行为核对来源：[Codex 命令](https://learn.chatgpt.com/docs/developer-commands)、
[Codex 插件打包](https://developers.openai.com/plugins/build/plugins)、
[Claude Code 插件](https://code.claude.com/docs/en/discover-plugins)、
[Cursor 插件](https://cursor.com/docs/plugins)。


状态待同步：BLE 已连接与 Codex 真实事件送达是两项独立检查。核对实际 Hooks 列表和
私有适配器记录的更新时间。安装前创建的聊天可能需要新开聊天，或在没有活动任务时
重载宿主；新配置检查通过不能证明已有聊天已加载。没有真实事件时应报告待验证，
不要注入假的忙碌/空闲状态。


### 不需要逐会话安装或信任 Hooks

用户级插件安装一次即可；未变化的 Hook 定义按宿主保存的哈希信任，不需要每个聊天
重新配置。只有新定义或定义改变时，才可能再次要求审阅。不要将反复运行 Setup、
复制项目级 Hooks 或新建聊天当作“状态待同步”的常规修复。

**不要仅凭全局设置页的空列表判断 Hooks 未安装。** 目前桌面版本在没有已添加项目
目录时，不会发起全局 Hooks 查询；此时显示“未找到钩子”，刷新按钮也不可用。
这是设置页的枚举限制，不是每个聊天缺配置，也不能靠反复重启证明已解决。如果用户希望在这个
全局页面管理 Hooks，可通过「文件 → 打开文件夹」将固定保存的雪球仓库添加为一个
项目，然后返回「设置 → 钩子」。已在 Windows 单机确认页面随后显示
`Snowball Companion / 10 个钩子 / 10 项待审核`；打开该项后，用户可审阅并点击
「全部信任」。只需一个项目根目录，不需要每个聊天添加项目，也不应修改应用安装资源。


优先使用当前聊天输入框旁的「审查钩子」入口。它按当前聊天的实际配置显示待审阅
Hooks；点「详情」核对来源 `xueqiu-status`、解释器和脚本路径，再由用户确认。
显示“已修改”的定义在审阅前不会运行；不能自动改写 `trusted_hash`，也不能替用户
点击“全部允许”。已有项目时也可使用「设置 → 钩子」；无须为了显示列表给每个聊天
创建项目或复制一套 Hooks。需要 CLI 时用 `/hooks`；新 CLI 的列表仍不能替代真实
事件与 Hub/设备验证。

只有实际宿主配置加载出现问题，并且当前聊天审阅入口也没有正确配置时，才继续
排查宿主与执行模式。重载应在活动任务结束后进行，不应把重启当作默认下一步。

官方说明：[Hook 来源与信任规则](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)。
云端编排即使允许本地工具，也不支持本地配置/插件中的命令 Hooks；使用该模式时，
反复安装和信任不能让它们生效。先核对实际模式，不能仅凭界面名或新 CLI 的结果推断。


### Windows 已信任但无状态记录：先检查原生 Shell 启动

不能只直接运行 Python 脚本来验收命令 Hook。早期 Windows 配置把带引号的 Python
路径直接写入通用 `command`，经 `cmd /D /S /C` 或 PowerShell 的实际启动路径时会
解析失败，表现为 Hooks 已信任、BLE 在线，但没有真实适配器记录。

Windows 配置器现在保留通用命令，使用官方 `commandWindows` 覆盖：简单绝对路径
采用无需引号的固定参数；空格、中文或单引号路径采用本机生成的 PowerShell 编码
调用，执行相同的固定 Python 脚本并传递 stdin。绝不把事件正文拼入命令。
回归测试经两个原生 Shell 传入隔离 JSON，覆盖简单路径及空格/中文/单引号路径。

更新启动命令后需要用户再审阅一次变化的定义；不修改保存的信任哈希，也不自动批准。
之后再检查真实 Hook → Hub → BLE 设备回执，不能把直接调用 Python 的成功结果
或隔离测试当作真实聊天已同步。
