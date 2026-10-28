# 雪球蓝牙电脑端

0.17.0 新增全部本机 Codex 用户会话目录、空闲会话布置任务和确认后创建独立聊天；网页与设备同步。当前安装中的原生应用工具负责投递，目录读取不启动任务。操作与验证边界见 [会话与任务](../docs/SIDEBAR-TASKS.md)。下文手动状态控制台仅用于演示，不是已安装的真实适配器。

从原生 0.16.0 开始，只有进入「设置 → 连接电脑」（或首次教程连接步骤）才公开广播 `Xueqiu-XXXXXXXX`，窗口为两分钟。连接成功、退出连接页面或超时都会停止公开配对。已绑定电脑在断线时仍可通过受限广播自动重连；已连接时不广播。本机已通过 USB 核对设备身份，再完成 BLE 绑定；之后状态推送不需要 USB 数据连接，手表保持供电即可。本轮先实现蓝牙，Wi-Fi 未启用。

## 直接操作

在此目录运行：

```sh
./connect-bluetooth.sh control
```

终端中输入数字后回车：`1` 待机、`2` 处理中、`3` 执行中、`4` 等待确认、`5` 完成、`6` 异常。`p` 切换 Codex / Claude / Cursor，`q` 断开并退出。程序会每 5 秒发送心跳；链路中断后自动重试连接。安装了 uv 时脚本会使用固定缓存目录中的隔离环境安装固定版本依赖，后台进程不依赖临时环境；否则先安装 `requirements.txt`。

这是真实 BLE 传输的**手动状态控制台**。尚未自动读取 Codex、Claude 或 Cursor 的任务与授权事件，也不会代替用户批准工具调用。网页 APP 暂未接到这条数据通路。

表盘连接文字含义：

- 正在配对：公开广播开启，两分钟内可在电脑蓝牙中发现。
- 等待已配对电脑重连：公开配对关闭，仅接受已绑定电脑。
- 配对已关闭：没有已绑定电脑，需进入连接页面开启配对。
- 蓝牙已连接：BLE 链路建立，尚未收到合法状态。
- 蓝牙同步：已收到绑定电脑的合法状态及心跳。
- 连接已过期：链路仍存在，但超过 15 秒没有有效更新。
- 连接已断开：已有过状态同步，后来失去蓝牙链路。

系统设置中的“已配对”表示保存了绑定；是否持续同步，还取决于电脑端工具是否运行。固件同时只接受一个 BLE 连接。

## 新电脑首次绑定

安装 Python 3.10+，并在自己的虚拟环境安装依赖：

```sh
python3 -m pip install -r requirements.txt
python3 bridge.py pair-usb --port /dev/ttyACM0
python3 bridge.py control
```

首次先在设备进入「设置 → 连接电脑」，再通过 USB 读取该设备的应用配对密钥，保存在 `~/.config/xueqiu/ble-pairing.json`（目录 0700，文件 0600）。脚本不在终端打印密钥。**配对文件不要加入项目、ZIP 或共享给其他人。** 开发包本身不包含设备密钥；另一块板或另一台电脑需自行绑定。

Linux 工具只为选定的雪球注册临时配对 Agent，不改变系统默认 Agent、不操作其他设备。当前这台 Linux 桌面已实测；Windows/macOS 路径保留 Bleak 原生配对能力，但尚未实测。

## 命令与后续软件接入

```sh
python3 bridge.py scan
python3 bridge.py check
python3 bridge.py send --provider Codex --state completed --hold 30
python3 bridge.py demo
python3 bridge.py watch --source example-status.json
```

`watch` 读取本地 JSON 文件。下面精确两个字段的格式保留给**手动演示**，会持续发送，不应作为真实 AI 适配器输出：

```json
{"provider":"Codex","state":"running"}
```

支持 provider：`Codex`、`Claude`、`Cursor`；state：`idle`、`thinking`、`running`、`waiting`、`completed`、`failed`。建议通过临时文件 + rename 原子更新。

真实软件适配器应通过本地 status hub 写出有期限的快照，再显式选择它：

```sh
./connect-bluetooth.sh start --source "$HOME/.local/state/xueqiu/hub/snapshot.json"
# 或前台运行；确保没有另一个电脑端工具占用手表
python3 bridge.py watch --source "$HOME/.local/state/xueqiu/hub/snapshot.json"
```

这里只启动 BLE bridge；status hub 与软件适配器需要各自启动，`start --source` 不会代替它们创建“正在运行”的假状态。若已有后台 bridge 使用其他源，请先 `stop` 再选择新源。

Bridge 消费 `version: 1` 的 hub 快照，要求 `source_fresh`、`task_fresh`、`hub_alive` 都为 `true`，`status` 为 `known`，且存在合法 `provider` / `state`。`generated_at`、`updated_at`、`task_updated_at`、`expires_at`、`task_expires_at` 都是 Unix 秒。会拒绝非有限时间、超过 5 秒的未来时间、失序时间，以及过期的来源或任务；来源 TTL 必须在 5～300 秒内。

这里有三个独立的时间概念：

- **任务事件时间** `task_updated_at`：只由来源的有效任务事件更新，bridge 不修改。
- **来源心跳**：表示观察器仍在工作，不能让旧的 `running` 无限续期；任务自身的 `task_expires_at` 仍独立生效。
- **BLE 传输心跳**：仅当快照及任务均有效时，每 5 秒重发当前枚举以维持手表的 15 秒同步有效期。序号只用于传输确认，不代表又发生了一次任务事件。

源文件不可读、格式无效、来源失联或任务过期时，bridge **停止所有状态写入**。已建立的 BLE 链路每 5 秒只读取鉴权信息检查连接，读取不续手表的状态 TTL；故障仍会进入自动重连。手表会在最后一次有效状态写入后的 15 秒显示“状态待同步”。过期记录里的 `last_state` 只供回顾，不会被当作当前状态发送。重复等待原因只记录一次，恢复有效快照后立即继续。

## 协议与校验

- BLE Service：`9e7a0001-6ed4-4b6b-bf37-43dc42838c20`
- 信息/已应用回执：`9e7a0002-6ed4-4b6b-bf37-43dc42838c20`，JSON Read。
- 状态写入：`9e7a0003-6ed4-4b6b-bf37-43dc42838c20`，Write with response，要求加密链路。
- 数据包 47 字节：`<BBBI8s`（版本、provider、state、递增序号、8 字节启动 nonce）+ 32 字节 HMAC-SHA256。
- 长属性读取会固定整个回执快照，避免分段之间状态更新造成签名不一致。
- 信息回执也有 HMAC：`XQINFO` + `<8sIIBB`（nonce、accepted、applied、provider、state）。电脑必须先验证设备身份与回执，再认定传输成功。
- 接收线程仅验证并入队；LVGL 线程处理后才更新 `applied`。电脑等待该序号回执，避免把 GATT 写入成功当作界面处理成功。
- 原始应用密钥仅经显式 USB `:PAIR` 指令读取，不通过广播或 BLE 发送。状态传输新增用户授权的显式提问标题，不发送答案、源代码、凭证或其他任务正文。
- 序号重复、旧启动 nonce、超范围状态、错误长度或签名都会被拒绝。错误签名使用应用错误码 0x80，避免误触发系统 SMP 重新配对。

原厂 app0 与资源分区仍保留。本版会在已有 NVS 的 `xueqiu` 命名空间保存应用密钥/设备 ID，并由 NimBLE 保存绑定记录；不会为了初始化蓝牙擦除整个 NVS。开发包不包含运行时 NVS 内容。

固件使用 Espressif [NimBLE API](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-reference/bluetooth/nimble/index.html)，PC 使用 Bleak 3.0.2。

## 保持后台连接（本机 Linux）

```sh
./connect-bluetooth.sh start
./connect-bluetooth.sh status
./connect-bluetooth.sh stop
```

`start` 不带 `--source` 时，在当前登录会话启动独立后台进程，持续发送 `~/.local/state/xueqiu/desktop-state.json` 中的状态；首次默认为 Codex / idle。`start --source 路径` 使用显式文件，`status` 会显示所用源路径。它不会安装开机自启服务。日志在同目录 `bridge.log`。后台发送仍是手动状态来源，不代表自动读取 AI 软件。

打开 `control` 会先停止此安装管理的后台进程或用户服务，再进入交互控制，避免两个连接争用手表。关闭控制台后若要持续连接，再运行 `start`。停止进程时会校验 PID、启动时间与脚本路径，只操作本工具创建的进程。


## 电量、设备状态与续航日志（原生 0.11.0）

表盘下滑进入设备状态。新版桥接器可独立读取经过签名验证的电量遥测，在 AI 会话过期时仍记录供电和电量；校时不延长 AI 状态有效期。电脑端记录位于 `~/.local/state/xueqiu/device/`，网页本地预览状态页支持显示和下载。ESP32 SD 同时记录离线使用情况。V1 的电量/供电为估算，AXP2101 使用已有电量计；不会改写充电参数。完整协议与边界见 `POWER-STATUS.md`（原生源码中为 `docs/POWER-STATUS.md`）。


0.12.0授权范围更新：新增用户明确要求的「提问工具的问题标题」与独立问题ID，不含答案、任意工具输出或命令。支持按问题暂缓600秒、忽略和恢复；不执行回答或批准。正常空闲由在线Hub明确报告，运行状态失效仍保留未知。详见随包 ATTENTION-DETAILS.md。


语音输入（原生 0.15.2）：可选电脑端本地识别与设备录音已加入。初次启用运行随包 setup-speech.py。发送到已有本机 Codex 会话或对应待答问题之前必须确认识别文字。约 5.7 秒真人录音实测上传 2.236 秒、模型识别 2.468 秒；结束后 BLE 恢复原连接间隔。Hub 失效问题的 409 拒绝与语音识别故障分开处理，未知投递不重发。完整说明和验收边界见 [语音输入](../docs/SPEECH-INPUT.md)。


## 登录后自动恢复（Linux，0.16.0）

先完成自己的设备绑定，并固定安装目录。需要登录自启时，在桥接虚拟环境中显式运行：

```sh
python user_services.py install
```

会安装当前用户的 `snowball-hub.service`、`snowball-bridge.service`，在用户登录时启动，异常退出后恢复。仅使用真实 Hub 快照，不自动创建演示状态。不会启用系统级服务或无人登录常驻。已有正常环境可继续使用原来的临时后台模式。

`manage.py` 和 `manage_hub.py` 的 start/status/stop 会识别本安装拥有的服务。手动 stop 不会自动重启，因此烧录、串口维护仍可可靠暂停同步。不会接管别的安装或同名外部服务。安装失败可修正后重试；安装命令验证两个服务均 active。

Linux 重连优先从 BlueZ 的已连接/已绑定设备中定位精确设备名，再验证应用 HMAC；不再要求设备重新公开广播。缓存没有匹配时才扫描。系统蓝牙「已连接」仍不代表 AI 状态已同步，需同时有新鲜 Hub 数据和设备应用回执。
