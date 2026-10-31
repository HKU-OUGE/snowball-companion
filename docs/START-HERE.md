# 第一次使用 Snowball Companion

先选你实际使用的软件。**只想使用 Codex 的桌宠时，不需要连接 ESP32。** Claude Code 和 Cursor 插件主要同步工作状态，不会在 ChatGPT 账号里自动创建 Pet。

## 1. 下载对应插件

从 [v0.5.0 下载页](https://github.com/HKU-OUGE/snowball-companion/releases/tag/v0.5.0) 下载对应 ZIP，也可以克隆仓库。解压后保留全部目录。

| 软件 | 安装包 | 解压后进入 |
| --- | --- | --- |
| Codex Desktop / CLI | `Snowball-Codex.zip` 或 `Snowball-Codex-Core.zip` | `codex/` |
| Claude Code Desktop / CLI | `Snowball-Claude.zip` | `claude/` |
| Cursor | `Snowball-Cursor.zip` | `cursor/` |

Core 只包含雪球基础 Pet，完整包提供更多角色。两者使用同一个 Codex 插件 ID，选一份安装即可。将安装包复制到电脑固定目录再解压，不要长期从临时目录、下载缓存或可拔出的 USB 安装盘运行共享服务。

下载配套 `.sha256` 文件后，在 ZIP 所在目录核对：

```sh
sha256sum -c Snowball-Codex.zip.sha256
```

macOS 可使用 `shasum -a 256 -c`。替换为实际下载的文件名。

## 2. 安装电脑插件

以下命令以 Linux 为例。先安装对应软件并确认终端能找到其 CLI；原生 Windows 不适用这些 shell 命令。已有 Snowball 服务时先看 [安装、升级与卸载](INSTALLATION.md)，避免启动第二套服务。

**Codex：**进入解压目录中的 `codex/`，执行：

```sh
./install.sh
codex plugin list --json
```

确认启用了 `xueqiu-status@xueqiu-local`。新开聊天，运行插件 Setup／设置，或说“设置 Snowball Companion 并启用雪球”。Pet 由宿主 Pets 服务创建；如需连接服务或审阅 Hooks，按提示完成。安装 ZIP 本身不会静默写入账号 Pet 库。

**Claude Code：**进入 `claude/`，执行：

```sh
claude plugin marketplace add .
claude plugin install snowball-companion@snowball-local --scope user
```

新开本地 Code 会话后运行 `/snowball-companion:setup`。普通 Claude 聊天、远程 Code 会话和本地 Code 插件不是同一个接入目标。

**Cursor：**进入 `cursor/`，按目录内 README 将 `snowball-companion/` 安装到 `~/.cursor/plugins/local/`。目标存在时先核对并备份，不混合覆盖新旧文件。等任务结束后重载窗口，在插件设置页核对加载。

## 3. 可选：连接雪球圆屏

需要运行兼容 Snowball 固件的设备、电脑依赖及本机 Hub。插件包没有设备固件、配对密钥或 Wi-Fi 密码，也不会刷写设备。不要把任意 ESP32 的串口当成雪球。

已有 Hub／蓝牙桥接服务时直接复用。新安装按本平台 README 启动配套服务，再核对实际设备名称和串口完成配对。在设备“设置 → 连接电脑”开启公开配对广播，连接后检查设备状态页。Linux 依赖位于 `companion/requirements.txt` 或 `runtime/requirements.txt`，建议使用独立 Python 虚拟环境。

## 4. 分四层确认成功

1. 软件的插件管理页显示已启用。
2. 你实际运行任务后，插件收到真实事件。
3. 本机 Hub 会话状态发生变化。
4. 如果连接了设备，设备在线并显示相应变化。

`doctor` 成功或 Hub 可达只证明部分链路。Claude／Cursor 的可观察状态与 Codex 不完全相同，按本平台 README 判断支持范围。

## USB 安装盘与后续更新

在支持安装盘功能、且 SD 卡里已有公开安装资源的设备上，可以不经过网页下载，直接用数据线复制插件：

1. 用支持数据传输的 USB 线把雪球接到电脑。
2. 在设备进入 **设置 → 连接电脑 → 获取插件**。这里也有公开安装指南二维码。
3. 点 **USB安装盘**，等待准备完成。电脑会出现名为 `SNOWBALL` 的只读磁盘。
4. 打开 `START.HTM` 看离线说明，或复制 `CODEX.ZIP`、`CLAUDE.ZIP`、`CURSOR.ZIP`。`CODEX.ZIP` 是只包含雪球的 Core 版；完整角色库使用上方线上下载入口。
5. 把 ZIP 复制到电脑固定目录，完成复制后弹出磁盘，或在设备点 **退出安装盘**。设备会重启，恢复正常连接；然后在电脑解压并按包内教程安装。

安装盘模式会暂时替代 USB 串口。语音录音、资源上传或固件更新正在进行时，先等当前操作结束再打开。普通连接数据线不会自动运行安装脚本，也不会把整张 SD 卡挂载给电脑。

这个只读盘只包含公开的插件包、说明和校验文件。Wi-Fi、配对密钥、会话及其他 SD 资源不在安装盘里。主机不能通过它更改设备文件。Python、对应 AI 软件和第三方依赖没有一并内置，因此不承诺在全新电脑上完全离线完成所有依赖安装。

这项功能需要兼容的设备固件和安装盘镜像；只有电脑插件 ZIP 或 USB 串口不能证明设备已经支持。安装盘里的包可能早于线上版本，需要最新版时查看 [Releases](https://github.com/HKU-OUGE/snowball-companion/releases)。

电脑插件更新与固件更新分开处理，见 [更新说明](UPDATES.md)。普通卸载只移除指定平台插件；账号 Pet、其他平台、设备配对和共享服务分别管理。
