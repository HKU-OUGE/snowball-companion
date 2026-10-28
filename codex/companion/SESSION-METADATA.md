# 会话显示名称与 BLE v2

已授权元数据只有软件、工作阶段、匿名会话标识和有限长度显示名称。无名称时显示未提供，不从提示词或目录猜测。当前 Codex 插件以精确 session ID 查询只读本机 threads.name（旧schema仅有title列时使用title）；Claude / Cursor 未提供名称时仍可正常同步阶段。

## 单包布局

| 字节 | 内容 |
|---|---|
| 0 | 版本 2 |
| 1, 2 | provider 0..2、state 0..5 |
| 3..6 | little-endian uint32 递增 seq |
| 7..14 | 当前启动的 8 字节 nonce |
| 15..30 | 16 字节匿名 session hash |
| 31 | UTF-8 名称字节数 0..192 |
| 32.. | UTF-8 显示名称，最多 64 个 Unicode scalar |
| 最后 32 字节 | HMAC-SHA256(key, 上述所有字节) |

长度为 64 + 名称字节数，最大 256 字节；使用加密 GATT write-with-response，较小 MTU 由 GATT 长写处理。v1 的 47 字节状态包仍支持，收到 v1 应清除当前元数据，防止沿用前一会话名称。v2 无名称仍携带 session ID 和长度 0，不虚构标题。

保留启动 nonce、严格递增 seq、常量时间 HMAC 对比和 UI applied 序号回执；篡改名称或 ID 将导致 HMAC 拒绝。原生端严格检查 UTF-8、码点数量、长度和常见控制字符，非法内容不会进入 UI。info 认证保持 v1 兼容。新桥接需要先部署新固件；不对旧固件静默降级丢弃名称。

桥接比较 provider/state/session_id/display_name，只要其一变化就立即写入。同一快照的元数据变化不会改写 task 时间戳或有效期。过期仍停止写状态并只做连接探测，名称不会延长任务寿命。记录和密钥保存在本机私有目录，发布包不包含它们。


0.17.0 的完整目录是独立的 v3 catalog 通道，紧凑 INFO 使用v5；设备支持64项，已不受早期8项限制。原状态v2包不变。创建能力、原子状态增量、短租约续期和49字节排序编码见 [SIDEBAR-TASKS.md](../docs/SIDEBAR-TASKS.md)。
