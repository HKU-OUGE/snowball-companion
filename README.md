# Snowball Companion

**Codex 0.6.0 Windows 原生版正在本分支验证：** [安装、首次绑定、后台与诊断](codex/docs/WINDOWS.md)。下面 v0.5.1 链接仍是已有稳定包；不要把它当作新的 Windows 安装器。

雪球是 AI 工作时的桌面伙伴，可选连接 ESP32 圆屏。这里提供 **Codex、Claude Code 和 Cursor 三个独立的电脑插件**及使用教程；不包含网页应用、设备固件或云端服务源码。

## 下载与安装

**第一次使用，请从 [新手指南](docs/START-HERE.md) 开始。** 已安装用户请先看 [升级与卸载](docs/INSTALLATION.md)，保留已有配对与共享服务。

本次准备 **v0.5.1** 安装包；请先在 [Releases 页面](https://github.com/HKU-OUGE/snowball-companion/releases) 确认该版本已发布。下列链接在资产发布后可用，源码或本地构建完成不代表已经发布。

| 软件 | 下载 | 说明 |
| --- | --- | --- |
| Codex Desktop / CLI，完整角色库 | [Snowball-Codex.zip](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.5.1/Snowball-Codex.zip) | [Codex 0.5.1](codex/README.md) |
| Codex，轻量 Core | [Snowball-Codex-Core.zip](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.5.1/Snowball-Codex-Core.zip) | 只包含雪球基础 Pet，安装方式相同 |
| Claude Code Desktop / CLI | [Snowball-Claude.zip](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.5.1/Snowball-Claude.zip) | [Claude 0.2.1](claude/README.md) |
| Cursor | [Snowball-Cursor.zip](https://github.com/HKU-OUGE/snowball-companion/releases/download/v0.5.1/Snowball-Cursor.zip) | [Cursor 0.2.1](cursor/README.md) |

每个 ZIP 有配套 `.sha256` 文件，Release 同时提供 `SHA256SUMS`。解压后保留完整目录，不要只复制单个 Hook。Core 构建时强制小于 8,000,000 字节。若要从设备 USB 安装盘复制这些包，需要相应设备固件；本仓库不声称已经部署该固件。

新固件的手动蓝牙更新需要新版电脑插件：Codex 0.5.1、Claude／Cursor 0.2.1。新版兼容老设备的 v1 协议。设备 SD 安装盘中的旧离线包没有被本次源码更新替换；从离线包安装后，请联网按 [升级说明](docs/INSTALLATION.md) 更新插件。

## 能力与验证边界

| 平台 | 安装与功能 | 验证范围 |
| --- | --- | --- |
| Codex Desktop / CLI | [Codex 插件说明](codex/README.md) | Linux 本机插件和设备集成曾测试；不同版本与系统的兼容性需自行验证 |
| Claude Code Desktop / CLI | [Claude 插件说明](claude/README.md) | 官方插件结构与隔离环境测试通过；真实登录会话和设备联动尚未端到端测试 |
| Cursor | [Cursor 插件说明](cursor/README.md) | Linux 隔离测试通过；Cursor 实际加载和设备联动尚未端到端测试 |

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
