# Windows 文件与服务兼容检查

本地 Windows 修复覆盖 OTA 缓存读取、网关身份落盘、应用通道回退、旧服务入口和私有文件权限。
完整包仍需分别验收插件、真实 Hooks、Hub、BLE、语音和用户确认后的投递。

## 文件 API 与权限

- `companion/private_files.py` 在 Windows 使用原生文件句柄读取，拒绝符号链接和目录联接等重解析点；不调用不存在的 `os.O_NOFOLLOW` 或 `os.fchmod`。
- 敏感目录/文件使用受保护的 DACL，只授予当前用户和 SYSTEM 完全访问，并从真实句柄读回 ACL 验证。已有 ACL 符合要求时不重复设置，避免干扰并发读取。
- 目录联接应报告不支持，不能先 resolve 链接后忽略它，也不能降级成普通 open 来“恢复可用”。Windows `chmod(0600/0700)` 不是 ACL 保护。
- 凭据写入使用私有目录内的随机临时文件，刷盘后原子替换；保留已有网关身份，不因升级重新生成密钥。
- OTA 缓存命中仍须验证签名、目标版本、长度和 SHA-256，损坏缓存重新下载。测试缓存逻辑不授权实际安装固件。
- 活动 Hooks/Pet 脚本使用随包 `scripts/private_files.py`；与运行时 helper 内容保持一致并加入打包清单。

## 服务与进程

Windows 使用 `companion/windows-services.ps1` 和 Windows supervisor。
`manage.py`、`manage_hub.py`、`user_services.py` 的 Linux 入口在执行前拒绝 Windows，
避免 `/proc` 身份检查、fcntl 或 systemd 误用。`bridge.py control` 同样拒绝 Windows。
不要用旧管理器“试着停止”进程；按实际脚本路径和当前用户核对归属。

语音 worker 在 Windows 使用 `CREATE_NO_WINDOW` 与 `-X utf8`；Unix 才使用
`start_new_session`。共享服务更新前先确认无录音上传或固件传输，不重启 Codex 活动聊天。

## 应用通信与验证边界

命名管道不可用时，新指令直接返回 `desktop_unavailable`，不回退 Unix DesktopIPC。
确认发送和问题回答的精确身份与去重规则见 [Windows 投递](windows-send.md)。
宿主重启后端点变化、登录任务未继承新端点的限制仍存在，不能声称已自动发现新端点。

隔离回归包括真实 DACL 读回、撤销临时 Everyone 权限、中文路径、目录联接拒绝、
缓存损坏重验、Windows 禁止 Unix 回退、旧入口执行前拒绝及并发 JSON 发布。
通过这些测试不等于已在真实设备执行 Wi-Fi 配网或固件更新。
