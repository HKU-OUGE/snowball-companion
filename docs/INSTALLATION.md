# 安装、升级与卸载

本文适用于本仓库的 Codex、Claude Code 和 Cursor 适配器。**Codex 原生 Windows 用户先看 [Windows 向导](../codex/docs/WINDOWS.md)**：安装、绑定、后台与诊断都有独立入口。下方 shell 命令以 Linux 为例；
先在当前机器查看 CLI 的 `--help`。Claude 和 Cursor 的 macOS 设备联动、原生
Windows 安装不能仅凭这些命令视为已验证。最后核对日期：2026-11-01。

## 先分清三层

| 层 | 用途 | 卸载一个插件时的默认处理 |
| --- | --- | --- |
| 软件适配器 | 接收 Codex / Claude / Cursor 的状态事件 | 只移除用户指定的适配器 |
| 共享伴随服务 | Hub、蓝牙桥接、Wi-Fi 网关、云端连接 | 保留，其他客户端可能还在使用 |
| 个人数据与硬件 | 配对、密钥、Pet 库、会话设置、固件、SD 资源 | 保留，不随插件卸载擦除 |

安装插件与启用账号里的 ChatGPT Pet 也是两个动作。只有 Codex 版提供现有的
Pet Setup 流程；Claude / Cursor 主要同步状态，不能把它们安装成功写成桌宠已创建。

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
