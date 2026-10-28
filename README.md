# Snowball Companion

雪球是 AI 工作时的桌面伙伴，可选连接 ESP32 圆屏。这个仓库分别提供 Codex、Claude Code 和 Cursor 的本地插件。

| 平台 | 安装与功能 | 验证范围 |
| --- | --- | --- |
| Codex Desktop / CLI | [Codex 插件说明](codex/README.md) | Linux 本机插件和设备集成曾测试；不同版本与系统的兼容性需自行验证 |
| Claude Code Desktop / CLI | [Claude 插件说明](claude/README.md) | 官方插件结构与隔离环境测试通过；真实登录会话和设备联动尚未端到端测试 |
| Cursor | [Cursor 插件说明](cursor/README.md) | Linux 隔离测试通过；Cursor 实际加载和设备联动尚未端到端测试 |

克隆后进入所需目录，按照对应说明安装：

```sh
git clone https://github.com/HKU-OUGE/snowball-companion.git
cd snowball-companion
```

各平台能力并不完全相同。Codex 版提供桌宠设置和可选设备连接；Claude 和 Cursor 主要通过本机 Snowball Hub 同步可观察的状态。请查看各自说明中的权限、隐私与功能限制。设备配对信息、用户状态、Wi-Fi 密码和密钥不包含在仓库中。

本仓库尚未指定软件分发许可证；第三方依赖和角色素材的权利分别属于各自权利人。
