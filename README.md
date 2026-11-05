# Snowball Companion

首次连接实体设备请从[统一入门指南](docs/START-HERE.md)开始：配置看电脑控制台，连接后回雪球练习。蓝牙同步不要求先配置 Wi-Fi、语音或账号桌宠。


雪球是 AI 工作时的桌面伙伴，可选连接 ESP32 圆屏。这里提供 **Codex、Claude Code 和 Cursor 三个独立的电脑插件**及使用教程；不包含网页应用、设备固件或云端服务源码。

## 下载与安装

**第一次使用，请从 [新手指南](docs/START-HERE.md) 开始。** 已安装用户请先看 [升级与卸载](docs/INSTALLATION.md)，保留已有配对与共享服务。

当前版本 **v0.7.0** 提供无需 Skill 的安装程序，已内置 Python 与连接依赖。打开后在网页勾选软件、蓝牙绑定、登录自启动和语音模型，再按提示连接设备。个人信息只在本机填写；系统提权由操作系统弹窗授权。

| 系统 | 安装程序 |
| --- | --- |
| Windows 10/11 x64 | [下载安装 EXE](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.7.0/Snowball-Setup-0.7.0-windows-x64.exe) |
| Linux x64（glibc 2.34+ 桌面环境） | [下载安装包](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.7.0/Snowball-Setup-0.7.0-linux-x64.tar.gz) |
| macOS 12+ Apple Silicon | [下载安装 DMG](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.7.0/Snowball-Setup-0.7.0-macos-arm64.dmg) |
| macOS 12+ Intel | [下载安装 DMG](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.7.0/Snowball-Setup-0.7.0-macos-x64.dmg) |

[安装程序详细说明](installer/README.md) · [全部文件与 SHA-256](https://github.com/HKU-OUGE/snowball-companion/releases/tag/v0.7.0)。
Windows 本版开放 Codex 适配器；Claude Code / Cursor 原生 Windows 暂不可选。账号 Pet 仍需宿主账号授权，实体设备安装不依赖该步骤。

传统插件 ZIP 仍在同一个 Release 提供：**Snowball-Codex.zip / Snowball-Codex-Core.zip / Snowball-Claude.zip / Snowball-Cursor.zip**，适合已有 Python 环境或从 SD 复制安装。Codex 插件 0.7.0，Claude / Cursor 插件 0.2.7。SD 卡旧包不会随 GitHub 发布自动更新。

## 能力与验证边界

| 平台 | 安装与功能 | 验证范围 |
| --- | --- | --- |
| Codex Desktop / CLI | [Codex 插件说明](codex/README.md) | Linux 本机设备集成已验证；Windows 原生安装与 Hook／BLE 验证记录、自动测试和剩余限制见 [Windows 支持](codex/docs/WINDOWS-QA-2026-11-03.md) |
| Claude Code Desktop / CLI | [Claude 插件说明](claude/README.md) | Linux／macOS 适配器；原生 Windows 不支持。隔离测试通过，设备联动仍需单独验收 |
| Cursor | [Cursor 插件说明](cursor/README.md) | Linux 隔离测试通过；原生 Windows 不支持。实际加载和设备联动需单独验收 |

统一的安装、升级、卸载和共享服务清理步骤见 [安装与卸载](docs/INSTALLATION.md)；由 Agent 执行时先读 [AGENTS.md](AGENTS.md)。
电脑缓存、蓝牙发送、设备安装前提与限制见 [自动更新说明](docs/UPDATES.md)。可靠空闲证据目前仅覆盖本机 Codex Desktop；不能将其解释成所有软件或 CLI 都已空闲。Claude／Cursor 的独立 Hub 保持各自入口，不启动 Codex 轮询。

克隆后进入所需目录，按照对应说明安装：

```sh
git clone https://github.com/HKU-OUGE/snowball-companion.git
cd snowball-companion
```

各平台能力并不完全相同。Codex 版提供桌宠设置和可选设备连接；Claude 和 Cursor 主要通过本机 Snowball Hub 同步可观察的状态。请查看各自说明中的权限、隐私与功能限制。设备配对信息、用户状态、Wi-Fi 密码和密钥不包含在仓库中。

## 构建安装包

```sh
python3 tools/build_plugin_bundles.py --include-core
python3 -m pytest -q tools/test_plugin_bundles.py
```

构建器只读取 [明确源码清单](tools/plugin_bundle_files.json)，生成确定性 ZIP 与逐文件 SHA-256 清单，默认输出到仓库外的同级构建目录。它不会安装插件、修改设备或发布 GitHub Release。打包和主机测试不等于宿主、真实 Hook 或设备端到端验收。

本仓库尚未指定软件分发许可证；第三方依赖和角色素材的权利分别属于各自权利人。


### Windows 连接说明

Codex 原生 Windows 可使用 `codex/install-windows.ps1`；硬件绑定和登录自动重连
参见[Windows 安装流程](docs/INSTALLATION.md#windows-原生安装与自动重连2026-11-02)。
仅安装插件/启用桌宠不会启动硬件服务。本次源码修复不代表已发布新版 Release 或安装包。

## 内置设备控制台

完整包和 Core 包都随附控制台网页、启动器和局域网同步服务。Windows 安装 Codex 后可从桌面“雪球控制台”打开；也可在 Codex 包的 `codex/` 或 Claude／Cursor 包的 `console/` 双击 `Open-Snowball.cmd`。Linux/macOS 使用 `./open-console.sh`。无需 Node.js 或开发服务器，向导会带着完成 USB 身份绑定、蓝牙、Wi-Fi 配网和连接检查。详见[控制台使用指南](docs/CONSOLE.md)。

控制台可以独立于 Codex 运行；各 AI 适配器的 Hooks 支持范围仍按平台说明分别核对。
