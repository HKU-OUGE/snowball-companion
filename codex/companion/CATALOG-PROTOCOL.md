# 多会话目录与反向关注协议

兼容已有 v1 状态与 v2 状态+会话标题；共用绑定密钥、启动 nonce、严格递增的 accepted_seq。
v3 只更新目录，不延长任务状态的15秒租约，不更新 applied_seq。

所有写包：`<version:u8,provider:u8,op:u8,seq:u32le,nonce:8bytes> + body + HMAC-SHA256(header+body)`。
version=3，最多272字节。目录 begin/row/commit 必须串行、同provider/generation；5秒内完整提交后才替换可见目录。
重复/缺失行、重复ID、不合法UTF8、越界条目、旧seq、不同nonce、无效签名均拒绝。

- begin body `<generation:u32,count:u8,total:u16,flags:u8,focused_id:16bytes,ack_request:u32,lease:u8>`。
  flags bit0=pinned，bits1..=0无回执/1成功/2失败。count<=8。lease目前20秒。
- row body `<generation:u32,index:u8,id:16bytes,state:u8,active_lease_seconds:u8,title_bytes:u8> + UTF8 title`。
  标题<=64字符且<=192字节；active_lease_seconds为0表示非活动，最多20秒，不能超出来源和任务租约。
- commit body `<generation:u32>`。目录读取时依据单条租约实时过滤active；断链立即无效。

info 保留 v1 proof，另加 catalog_v=1 与 request 字段：seq/provider/generation/pending/id/proof。
request proof 签名二进制 `<nonce:8,request_seq:u32,provider:u8,generation:u32,pending:u8,id:16>`；自动关注 id=""，签名中为全零。
手表请求最多有效10秒；桥接只接受当前发送目录的generation和映射、尚活动的真实source/session_id。
提交HTTP关注前重新读取并校验签名请求。Hub再次检查会话仍有效；收到Hub正确身份确认后才通过目录commit回执。
固件还会核对request序号、软件与目标ID，避免显示“已关注”但实际关注不同会话。

桥接每秒最多读取一次请求信息；目录仅内容变化或8秒租约续期时发送。事务中利用ATT有响应写的成功返回推进seq，避免每行再读取长info。
不会为目录生成额外Hub事件或fsync；现有任务心跳节奏保持5秒。目录不包含提示词、路径、工具参数或对话内容。
本协议的会话选择仅改变显示焦点，不批准、取消或执行模型任务。

点击与目录更新的竞态：桥接保留每软件最多32个已完整发送的generation映射，逐项20秒有效。
请求可匹配仍有效的旧generation，不因后台刚发新目录而失败；之后另用最新Hub快照验证身份和活动状态，Hub提交时再验一次。
从未发送、超过租约的generation仍拒绝，不能利用历史目录关注已经结束的会话。
无可展示自动焦点时，wire中的16个零字节解码为 focused_id 空字符串；fresh completed/failed 保留可展示身份。
最初 catalog_v=1、未加入宠物回执时，INFO最大JSON（所有uint32最大值）413字节，低于ATT属性512字节限制，仍使用长读取快照避免分片期间数据变化。

## 0.9 会话优先级列表

上滑进入当前软件的会话列表。右侧手柄上下拖动，松手后经蓝牙确认保存；手表把首/末行拖到列表上/下边缘可跨页移动。网页另有上下按钮和键盘方向键。排序后自动进入“按顺序关注”；点会话正文仍可明确固定关注，再点底部按钮恢复自动。

自动模式依次选择：当前异常 > 等你回答/确认 > 思考/执行。同类按用户列表顺序；完成或空闲让出显示，恢复运行按原顺序归队；全部安静时显示最近的完成/空闲。过期运行/错误不会当作实时活动；尚未回答的问题继续保留待答。优先级按软件独立，软件仍在设置里切换。手表每次同步最多8条会话，优先包含活动会话；分页每页3条。完成/空闲在新鲜期内仍可见并可排序，过期记录退出显示但电脑端保留顺序，恢复更新后按原位置参与。

电脑端顺序保存在私有hub/sessions.json，重连或重启不会重新按消息时间排序。网页使用模拟数据并保存在浏览器，网页排序不直接操作真实电脑任务。拖动保持行身份稳定，拒绝列表成员/顺序改变、过期、签名篡改或旧请求，心跳不影响排序；排序ACK前不声称保存成功。

协议：catalog_v=2，逆向请求附action(0关注/1移动)与before(目标前插，空为末尾)，HMAC覆盖原34字节+action1字节+before16字节。电脑兼容旧v1关注请求；移动通过现有授权/v1/focus接口的provider/move/before字段保存，无需发送或保存会话正文。启用自动列表后，手表依照电脑端返回的当前会话显示，新的优先待答/异常仍能触发声音和息屏提醒。红黄1秒闪烁、绿灯1.5秒呼吸及完成常亮保持不变。

## 0.9.1 列表草稿、退出保存与翻页

本节取代0.9每次拖动即保存的行为。紧凑会话标题固定单行，以省略号截断，完整名称仍可在详情阅读。列表右侧上下箭头翻页；调整顺序只修改草稿，多次拖动/跨页不会发送排序或改变电脑端优先级。后台状态刷新和息屏/唤醒不提交草稿。

返回、下滑离开列表或点“保存并返回”时，一次提交最终顺序。等电脑确认后才离开；拒绝、过期或断连时仍留在列表并保留草稿，点“重试并返回”重试。列表成员变化时按身份合并：保留仍在列表内的草稿相对顺序，添加新会话、移除不再可用的会话。编辑中的会话行点击只保存并返回自动模式；没有编辑时仍可点选固定关注。

BLE使用catalog_v2的action2批量提交：id为空，before16字节编码[count,index0,...,indexN-1,0padding]，最多8行，是对应generation完整排列；51字节HMAC结构不变。桥接器验证签名、catalog lease、排列唯一性与成员一致性，向本机授权/v1/focus提交provider/order。Hub在同一把锁内一次写入排序并切到自动模式，未列出的会话和其他软件保留原位。网页采用同样的草稿/退出交互，但仍是浏览器模拟，不直接写电脑真实会话；本地存储失败时不丢草稿、不虚报已保存。

非对称1.5秒呼吸(400/800/300ms)、红黄1秒闪烁与亮度设置保持不变。


## Firmware 0.10.3 compact INFO transport

The INFO characteristic now emits wire `v:2` to stay within ATT's 512-byte attribute limit. `d` is device name, `n` nonce, `a` accepted sequence, `s` is `[applied, provider, state]`, and `h` is the unchanged full XQINFO SHA-256 HMAC. `r` is `[request_seq, provider, generation, pending_boolean, id, action, before, request_proof]`; `p` is `[pet_seq, pet_result, pet_id, pet_proof]`. Both additional proofs retain the existing complete HMAC messages and 64 hex characters.

The actual C formatter is tested with maximum uint32 values, all uint8 values at 255, a 23-byte device name, 32-hex request IDs and before IDs, and a 31-byte pet ID: **472 bytes**. It rejects oversized/truncated output before GATT emission. The response is still frozen across ATT fragments.

New bridge versions normalize compact v2 to the existing internal v1 dictionary before authentication and continue reading old firmware's v1 JSON. Old bridges reject wire v2 explicitly as a version mismatch. Invalid JSON/transport truncation retries the BLE connection; identity or HMAC failures remain fatal.
