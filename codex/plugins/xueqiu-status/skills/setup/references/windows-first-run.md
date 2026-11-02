# Windows 首次连接与从零验证

用于用户要求初始化实体雪球、新电脑连接新设备，或完整重置后重走流程。
先确认用户是否需要实体设备；只安装账号 Pet 时不要求接线或蓝牙。
按当前阶段逐步引导用户操作，完成一阶段再继续，不把一串命令当作安装向导。

## 准备电脑环境

先核对 Windows、Codex CLI、Python 和固定保存的完整仓库/安装包。缓存中的插件根目录
不包含完整安装器。检查现有 Hub、桥接、自启任务和配对文件，报告本次是首次绑定还是复用。
换到另一台设备时，不把旧设备的配对文件当成新设备的身份。

先安装插件与依赖，再让用户开启限时配对。从完整包的 `codex/` 执行：

```powershell
.\install-windows.ps1
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\companion\requirements.txt
```

已有可用的专用虚拟环境可复用；不覆盖其他产品的 Python 环境。
安装失败先解决具体错误，尚未准备好时不要求用户反复开启配对窗口。
账号 Pet 按主 skill 的 authenticated Pets 流程创建/选择，不能代替实体连接验收。

## 引导接线和开始配对

1. 提醒用户用**支持数据传输的 USB 线/串口线**连接雪球与电脑，确认设备供电、电脑蓝牙开启。
   若设备处于 USB 安装盘模式，先完成复制并退出该模式，恢复 USB 串口。
2. 枚举串口，例如运行虚拟环境的 `python -m serial.tools.list_ports -v`。
   通过设备管理器、插拔前后变化或设备描述确认目标 COM 口；多个候选时让用户核对，
   不固定使用开发机的端口，不尝试所有串口。未出现串口时先排查数据线、接口和设备驱动。
3. 环境和串口已准备好后，明确提醒用户：**在雪球的「连接电脑」界面启用「开始配对」，
   保持开启，再回复已开启。** 仅插入数据线或显示“等待已配对电脑重连”还未完成此步骤。
   用户已经明确完成当前这一步时直接继续，不重复要求。
4. 收到实际操作完成的回复后立即执行 USB 绑定；此时不再下载依赖：

```powershell
# 用刚核对的实际 COM 口替换 COMx。
& .\.venv\Scripts\python.exe -X utf8 .\companion\bridge.py pair-usb --port COMx
```

配对窗口约两分钟。返回 `open_connection_page` 时，向用户说明需要重新启用
**「开始配对」**并在窗口内重试，保持用户操作问题待回复；不要自动无限重试或清空密钥。
绑定只将当前设备的应用凭据写入本机私有目录，不打印串口响应、密钥或提交配对文件。
USB 绑定成功后还需建立 BLE 连接，并用应用 HMAC 确认设备身份。

## 启动与验收

用户要求登录后自动重连时，从 `codex/` 执行：

```powershell
.\companion\windows-services.ps1 -Action Install -Python (Resolve-Path .\.venv\Scripts\pythonw.exe).Path
.\companion\windows-services.ps1 -Action Status
```

未要求自启时，使用前台/临时监督器或已有服务，不擅自创建登录任务。
任务状态 Running 不等于 BLE 已连接，USB 绑定也不等于设备已应用状态。

- 核对插件启用及当前聊天实际 Hooks，变化的定义由用户审阅一次；不自动修改信任。
- 核对用户真实操作产生的 Hook 记录、Hub 的 `accepted` 和状态时间。
- 核对 BLE 认证、设备应用回执与新鲜遥测；无真实事件时明确报告待验证。
- 要求自动重连时核对任务归属并验证服务停止/启动后的重连。未实际重启电脑就不称冷启动验收。

没有配置过的电脑在启用配对后应从公开广播发现目标；已配对缓存路径只覆盖重连。
分别报告首次发现/绑定和缓存重连的验证结果，不能用后者证明前者。
连接完成后 USB 可按设备供电安排拔除，状态链路由 BLE 维持。

## 为首次连接测试完整重置

“只卸载一个插件”保留共享服务；用户明确要从零重走**整个设备初始化**时，应完成以下
范围的清理并报告结果，不能仅卸载 Pet/适配器就宣布已回到初始状态。
完整重置已获授权后，不重复询问这些已明确的清理动作。

1. 记录实际插件、运行时路径、任务动作、进程归属及当前配对目标，只记录身份定位信息，
   不输出密钥。检查其他适配器是否仍依赖服务；有未授权的其他客户端时先保留其依赖并说明。
2. 通过宿主卸载目标插件；用户也要求删除账号 Pet 时，通过 Pets 服务删除已核实的目标，
   不删除其他账号 Pet。保留旧聊天引用的缓存，不直接清空宿主配置或信任库。
3. 使用归属已核实的 `windows-services.ps1 -Action Remove` 停止 Hub/桥接并删除登录任务。
   核对任务不存在、相关进程退出及 4190 不再监听；不宽泛结束所有 Python 进程。
4. 在删除配对文件前，用其精确设备身份定位 Windows 中唯一的目标并取消系统蓝牙绑定。
   可使用系统设置或 WinRT `BluetoothLEDevice.device_information.pairing.unpair_async()`。
   BLE 接口枚举项的 `pairing` 可能不反映关联端点绑定；应使用 BLE 对象的
   `device_information`。需要 `UNPAIRED` / `ALREADY_UNPAIRED` 结果及独立读回，
   不根据设备列表中暂时消失推断成功。重名、未知身份或失败时保留定位信息并解决，不盲删设备。
5. 解析并核对本机实际路径、检查符号链接/目录联接，服务退出后只清除本次初始化的状态：
   `~/.config/xueqiu/ble-pairing.json`、同名临时文件、`hub-token`；
   `~/.local/state/xueqiu/` 内 `hub/`、`device/`、`codex-adapter/`、`windows/`；
   `$CODEX_HOME/state/plugins/xueqiu-status/` 内旧 Pet 收据、锁及本次 Hook 备份。
   不整目录删除未知配置、OTA/签名密钥或其他客户端数据；列出保留项。
6. 完整移除也包括本安装的空 `xueqiu-local` 市场来源时，通过官方 marketplace remove
   注销；先确认没有其他已安装插件依赖。源码包可留作重装，注册来源不等于插件已安装。
7. 最后分别核对账号 Pet、插件/Hooks、任务/进程/端口、Windows 绑定和应用凭据均符合
   请求范围。记录是否仍有其他适配器、环境变量覆盖、旧聊天引用或持久状态；不自动重装、
   重新配对或新建测试聊天，等用户开始下一轮安装。

此重置清除电脑端连接状态，不擦除设备固件、SD 卡或恢复设备出厂设置，也不改写宿主的
Hook 信任记录。验证全新电脑的依赖安装、系统权限和首次信任，需要新电脑/干净用户环境
实测；本机清理后重装不能证明所有 Windows 与新设备组合均已通过。

WinRT 依据：[DeviceInformationPairing](https://learn.microsoft.com/en-us/uwp/api/windows.devices.enumeration.deviceinformationpairing)
与 [BluetoothLEDevice](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothledevice)。
