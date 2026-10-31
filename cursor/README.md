# Snowball Companion for Cursor

## 0.2.0 更新

配套公共运行时与 Codex 0.5.0 同步，包含认证蓝牙更新客户端；Cursor Hooks、命令和入口保持独立。单独启动的 Hub 不启用 Codex 活动轮询，没有可靠空闲依据时不自动安装。详见 [新手指南](../docs/START-HERE.md) 和 [更新说明](../docs/UPDATES.md)。包构建和隔离测试不等于真实 Agent 或实体设备验收。

这是一个 Cursor 原生插件包，用于把本机 Cursor Agent 会话的可观察状态发送给现有 Snowball Hub，让雪球通过已有的蓝牙或 Wi-Fi 链路显示。插件版本 0.2.0。

## 安装

目前已通过 Linux 主机自动化测试；**尚未在安装了 Cursor 的电脑或实体设备上完成端到端验证**。

在已经安装 Cursor 和 Snowball Companion 的电脑上克隆本仓库：

```sh
git clone https://github.com/HKU-OUGE/snowball-companion.git
cd snowball-companion/cursor
```

将其中的 `snowball-companion` 文件夹完整复制到：

```text
~/.cursor/plugins/local/snowball-companion/
```

在仓库目录下也可以执行下面的命令；它会在目标已存在时停止，避免混合旧文件：

```sh
mkdir -p "$HOME/.cursor/plugins/local"
if [ -e "$HOME/.cursor/plugins/local/snowball-companion" ]; then
  printf '%s\n' '目标已存在，请先备份并手动移走旧版。'
else
  cp -a snowball-companion "$HOME/.cursor/plugins/local/snowball-companion"
fi
```

重启 Cursor 或执行 `Developer: Reload Window`，打开 Customize 查看插件及其 hooks。在设备设置中选择 **Cursor**；开始一个本机 Cursor Agent 会话后，已观察到的会话会出现在雪球的现有会话列表中。

在已复制的插件目录执行只读检查：

```sh
python3 "$HOME/.cursor/plugins/local/snowball-companion/scripts/control.py" doctor
```

Hub 可达只证明电脑上的状态服务正常；是否连接到雪球仍需看设备状态页。Python 3.10+ 和 `python3` 命令需要在 Cursor 启动时的环境中可用。Linux 已进行主机测试；macOS 使用同一 POSIX 实现但尚未实测，原生 Windows 不支持本版。

## 状态与限制

| Cursor 事件 | 雪球状态 |
| --- | --- |
| 新会话创建 | 待机 |
| 思考片段、回复片段、工具返回、压缩上下文 | 处理中 |
| 本轮 Agent 正常停止 | 本轮完成 |
| 本轮被用户中止 | 待机 |
| Agent 本轮错误终止 | 本轮异常 |
| 单个工具失败、超时或权限被拒 | 处理中；不等同于整轮异常或用户待答 |

Cursor 的 `preToolUse`、Shell/MCP 执行前钩子会参与权限决策。本插件不注册这些钩子，因此**不会自动批准操作**，也无法可靠观察工具执行开始。遇到长时间运行的工具时，设备可能继续显示上一条状态或在状态过期后等待同步，不能把它当作精确的“执行中”状态。

Cursor 文档中的这些观察事件没有提供已显示的权限确认或待答问题详情，本版**不会猜测“等你回应”**，也不支持在雪球上回答 Cursor 问题、批准权限、发送新任务或语音指令。Cursor Chat 的历史侧栏名称也没有从 Hook 获得：列表显示 `Cursor · 项目目录名 · 匿名短编号`，只包含安装后实际观察到的会话。

所有状态按会话独立保存；不同轮次使用 Cursor 的 generation ID 防止旧回调覆盖已完成或更新的状态。没有新事件时，状态证据 5 分钟后过期，不会用定时心跳冒充 Agent 仍在工作。中继的蓝牙链路心跳与 Agent 状态独立。云端 Agent、远程 SSH/容器中的 Agent 和多台电脑尚未实测或接入。

## 隐私与权限

仅向固定的本机 `http://127.0.0.1:4190/v1/events` 发送软件名、匿名会话 ID、轮次顺序、状态、时间和项目目录名。不会传输提示词、模型思考、AI 回复、命令、工具输出、邮箱、会话原始 ID 或转录路径；插件不会读取转录文件。Hub 凭据只从用户自己的私有 `~/.config/xueqiu/hub-token` 读取。禁用代理与重定向；锁和 HTTP 请求均有短超时，Hub 不可用时跳过且不补发旧事件。

插件没有预置设备配对凭据、Wi-Fi 密码或密钥，不修改 Codex/Claude 插件配置。角色选择通过用户主动执行 `/snowball-pet`（或 `control.py pet <角色 ID>`）请求现有 Hub；收到 Hub 回执并不证明设备已加载该角色。本版不自动安装 ChatGPT 桌宠。

## 尚未配置 Snowball 服务

插件包含与 Claude 版相同的配套 Hub 和桥接程序。已运行 Hub 的用户直接复用；新电脑可以在插件目录执行：

```sh
python3 scripts/run_hub.py
```

该前台进程只提供本机状态服务，需要保持运行；如果已有 Hub 或端口被占用，它不会替换现有进程。随后按已有 Snowball Companion 配对指引配置设备桥接：

```sh
python3 -m venv ~/.local/share/snowball-companion/venv
~/.local/share/snowball-companion/venv/bin/python -m pip install -r runtime/requirements.txt
~/.local/share/snowball-companion/venv/bin/python runtime/bridge.py pair-usb --port /dev/ttyACM0
~/.local/share/snowball-companion/venv/bin/python runtime/bridge.py watch --source ~/.local/state/xueqiu/hub/snapshot.json
```

串口路径 `/dev/ttyACM0` 仅为 Linux 示例，应先确认实际设备。独立配对、macOS 和 Cursor Agent 实机流程没有在本次打包中验证。卸载手动复制的插件时，先核对服务的 `ExecStart` 和前台进程是否引用该目录的 `runtime/`；若仍被使用，先迁移共享运行时或在用户要求停用整个产品时一并停止。退出 Cursor 后，将 `~/.cursor/plugins/local/snowball-companion/` 移出本地插件扫描目录作为备份，或删除已核对归属的这个精确目录，然后重新打开 Cursor 验证不再加载。不要删除整个 `.cursor` 配置；共享 Hub 与设备配对信息默认保留。插件状态位于 `~/.config/xueqiu/hook-state/cursor-agent-plugin/`。

接口依据：[Cursor Plugins](https://cursor.com/docs/plugins)、[Plugins reference](https://cursor.com/docs/reference/plugins)、[Hooks](https://cursor.com/docs/hooks)。这是独立第三方插件，并非 Cursor 官方发布。

## 开发与测试

在仓库根目录运行 `python3 -m unittest discover -s tests -v`。自动化测试使用隔离的本地服务和测试凭据，不代表 Cursor 或硬件端到端验证。

本仓库尚未指定开源许可证；第三方 Python 依赖各自遵循其许可证。
