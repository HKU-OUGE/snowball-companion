# Snowball 安装程序

下载安装程序后按提示打开本机网页，不需要 Skill、Python、Node.js 或 Git。
请选择匹配电脑架构的文件；这些安装器只安装电脑插件与伴随服务，不更新设备固件。

| 系统 | 文件 | 打开方式 |
| --- | --- | --- |
| Windows 10/11 x64 | `Snowball-Setup-0.7.0-windows-x64.exe` | 双击后打开功能选择网页 |
| Linux x64，glibc 2.34+ 桌面环境 | `Snowball-Setup-0.7.0-linux-x64.tar.gz` | 解压后运行 `Install-Snowball.sh`；文件管理器不支持执行时，在终端执行 `sh Install-Snowball.sh` |
| macOS 12+，Apple Silicon | `Snowball-Setup-0.7.0-macos-arm64.dmg` | 打开磁盘映像，打开 `Install Snowball.app` |
| macOS 12+，Intel | `Snowball-Setup-0.7.0-macos-x64.dmg` | 打开磁盘映像，打开 `Install Snowball.app` |

本版尚未配置 Windows 代码签名和 Apple Developer ID 公证。系统可能显示未知发布者；
先核对来自本仓库的 Release 和 SHA-256，再按系统提供的打开／安全设置流程确认。
安装器不会关闭 Gatekeeper、SmartScreen 或修改执行安全策略。

## 一条完整的流程

1. 打开安装程序，浏览器显示“让雪球怎样陪着你？”。仅打开不会注册插件或启动后台服务。
2. 勾选 AI 软件、实体设备连接、登录自启动、本地语音。语音可选择 Tiny、Base 或 Small。
   Windows 本版只开放 Codex；Claude Code / Cursor 的适配器在 Linux、macOS 提供，
   其原生 Windows 支持尚未完成，不会用一个可安装的壳假装兼容。
3. 点击“安装所选功能”。基础 Python 和蓝牙依赖已内置；语音模型按选项获取。
   AI 软件需事先安装并登录。找不到 Codex / Claude 的程序时，可在高级选项填写程序文件路径。
4. 用 USB 数据线接上雪球。首次教程由安装器自动尝试绑定。只有设备明确拒绝并要求确认时，
   才在设备上点击开启配对；无回复或串口权限错误不会被误报成需要点击配对。
5. 打开“连接与网络设置”，检查实际蓝牙连接、会话回执和录音服务。
   AI 软件若提示审阅 Hooks，由用户在该软件中确认。Windows 已配对不等于会话已同步。
6. 如需 Wi-Fi，在控制台填写 2.4 GHz 名称、密码；页面引导打开设备配网窗口并加密传输。
   不需要在聊天中粘贴密码或 API Key。

后续在开始菜单／应用列表打开 **Snowball Console**，可以重新检查、调整所选功能或配网。
未勾选的选项不会删除已安装功能；卸载范围见 [安装与卸载](../docs/INSTALLATION.md)。
设备宠物直接在设备设置中选择。ChatGPT 账号 Pet 的创建仍需宿主 Pets 授权，
这是账号功能，不影响无需 Skill 的实体设备安装。

## 系统授权

普通安装、用户登录启动项不要求管理员权限。Windows 局域网防火墙配置通过系统 UAC
征求授权；Linux USB 串口拒绝访问时，点击“授权 USB 访问”，由 polkit 弹出系统认证窗口。
这项权限只授予当前用户、当前检测到的一台 Espressif 串口，本次拔插后可能需要再次授权。
安装器不收集、传输、缓存系统密码。拒绝／取消授权不会清除已经完成的设置。
macOS 的本版流程仅使用当前用户目录及 LaunchAgent，不需要 sudo；蓝牙隐私许可由 macOS 管理。

## 保存位置和诊断

- Windows：当前用户的 LocalAppData / Snowball Companion / 版本号。
- Linux：`~/.local/share/snowball-companion/installer/版本号`。
- macOS：`~/Library/Application Support/Snowball Companion/版本号`。
- 私有诊断：`~/.local/state/xueqiu/console/installer.log`，不要公开上传完整日志。

新版本使用独立目录，不覆盖正在运行的旧版共享服务。遇到已有启动项冲突会保留旧服务并
显示待处理。安装完成、服务健康、真实聊天 Hook、实际设备同步是四项独立检查。

## 构建

`tools/build_installers.py` 在各系统原生 runner 构建，Python 运行时使用
`installer/runtimes.json` 锁定的 SHA-256。应用文件使用现有插件发布 allowlist；
不递归复制开发目录、凭据、设备标识、会话记录或固件。CI 执行原生导入和安装向导隔离测试。
真实蓝牙、系统授权弹窗和宿主账号登录仍需对应系统的实体环境验收。
