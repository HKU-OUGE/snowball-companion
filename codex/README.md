# Snowball Companion · 0.6.0

## 0.6.0：Windows 原生安装与绑定

Windows 用户请使用自带依赖的原生向导：[Windows 安装、后台运行、首次绑定和诊断](docs/WINDOWS.md)。首次 USB 绑定成功后，日常只需蓝牙与后台，不需要每次在聊天中执行命令。Windows 从手表回复／新建对话的 Desktop 控制仍未接入；状态同步与私有控制分别验证。

## 0.5.1：设备手动更新请求

支持新固件发起的手动检查和确认版本下载请求，蓝牙优先；电脑端继续兼容旧更新协议。检查只返回版本与公告，确认后才请求对应镜像，不静默替换已确认版本。Wi-Fi 回退由兼容设备固件处理。旧固件不会因安装插件自动获得新请求界面；本次包准备与主机测试不代表已完成实机验收。

## 0.5.0 更新

本版同步独立 Codex 活动核查、空闲依据、经过验签的固件下载缓存及蓝牙暂存客户端。下载与安装分开，设备自行决定安装时机；可靠活动覆盖只限本机 Codex Desktop。详见 [新手指南](../docs/START-HERE.md) 和 [更新说明](../docs/UPDATES.md)。

Core 包只含雪球基础 Pet；完整包包含角色库。两者沿用相同插件 ID。下面的历史设备版本记录不代表本次重新进行了实机验收。

**0.17.0 设备/电脑伴随端更新**：会话列表包含全部本机未归档Codex用户聊天，空闲聊天可主动布置任务，底部可确认创建独立聊天。网页支持文字或录音，设备使用既有语音输入；默认设置与权限不变。详见 [会话与任务](docs/SIDEBAR-TASKS.md)。

A little company while AI works. 雪球陪你工作，也可以连接桌面圆屏伴侣。

本插件包含两部分：**可切换的像素 Pet 角色库**（无需硬件）和 **可选的 AI 状态同步**（本机 Hub＋BLE 圆屏设备）。宠物名保留“雪球 / Snowball”；产品名不再使用“手表”。

## 安装后会发生什么

1. 安装插件：雪球 v2 透明精灵图、设置技能、Pets 服务声明及状态 Hooks 一起安装。
2. 在插件详情点击 **Setup / 设置**，或对 Codex 说“设置 Snowball Companion，安装并启用雪球”。若宿主提示连接 Pets 服务，按宿主流程完成连接。
3. 设置向导验证随包图片，为当前账号创建自己的雪球并启用。已有本插件安装的雪球会按账号归属核对后复用，不反复创建。

这是“安装后运行一次设置，即可自动完成 Pet 创建与启用”，**不是安装插件时静默写入账号 Pet 库**。官方支持 onboardingSkill 入口，没有承诺自动执行安装脚本。Pet 创建由接收者当前账号的 Pets 工具执行；首次服务连接/宿主授权提示不能跳过。

随包 PNG 为 1536×2288、73 个有效帧，已通过 Pets 校验；不依赖过期 URL，不包含作者的账号 Pet ID。安装标记只写入接收者私有状态目录；换账号后必须重新核对 Pet 归属。没有 Pets 能力的宿主会明确提示未完成，不伪报安装成功。

## 本地安装与升级

安装、升级或卸载的 Agent 操作规范见 [插件生命周期说明](plugins/xueqiu-status/skills/setup/references/plugin-lifecycle.md)。普通卸载只移除 Codex 适配器，账号 Pet、共享服务与配对数据分别管理。

保留此目录完整，在固定位置运行：

```sh
./install.sh
```

上述 shell 入口用于 Linux，需要 Python 3 和 Codex CLI。Windows 使用 [原生安装向导](docs/WINDOWS.md)，不运行此 shell 文件。Windows 原生 CI 与真实蓝牙硬件验收分别报告；macOS 设备集成仍需单独验证。

稳定技术 ID 仍为 `xueqiu-status@xueqiu-local`，以保留现有安装、设备配对和私有状态。显示名称为 **Snowball Companion**。升级器保留旧会话仍引用的 Hook 脚本，不改写 Hook 信任。若宿主要求审阅新的/变化的 Hooks，请在 `/hooks` 中按提示完成；重新加载插件后运行设置。不要为了加载插件中断正在运行的任务。

**0.4.1 本机运行时兼容修复（2026-10-15）**：本机 Codex 在同时存在根 `plugin.json` 和 `.codex-plugin/plugin.json` 时，插件显示已启用，但实际装载的 Hooks 为 0。隔离探针对照确认仅使用兼容入口会恢复全部 10 个 Hooks。现将标准格式原件保留为 `plugin.agent-plugins.json`，以 `.codex-plugin/plugin.json` 作为运行入口；没有删改 Hook 定义或扩大信任。官方重新安装后已实测 10/10 Hooks 启用且受信任，当前既有聊天的真实工具事件也恢复进入 Hub，不需要重新蓝牙配对。以后若恢复根标准入口，必须先用本机 `hooks/list` 验证实际加载，不能只看安装成功或插件已启用。

**2026-10-19 状态过滤补丁**：完成后生成输入框“下一句建议”的内部临时会话不再进入任务队列，修复“完成 → 处理中／会话尚未命名”。正常未命名会话、真实新任务和待答仍同步；无需更新设备固件。见 [原因、匹配规则与验证](docs/INTERNAL-SUGGESTIONS.md)。

若只通过插件页面安装，完整资源仍随插件包安装，设置入口指向 `plugins/xueqiu-status/skills/setup/SKILL.md`。当前含生命周期 Hooks，适合本地/仓库市场或文件包分发；并未发布到公共插件目录。

## 切换宠物

对 Codex 说“列出可用宠物”或“切换到边牧”。角色目录包含 14 个形象，老年乔瑟夫采用《星尘斗士》造型，两只猫为圆脸橘猫和蓝眼布偶。实际可安装状态以 `plugins/xueqiu-status/assets/pet/catalog.json` 与 `pet_setup.py list` 的校验结果为准：待制作的角色不会被当作完整动画安装，也不会在切换失败时偷偷换成雪球。

每个角色的已验证 Pet ID 分开保存在接收者私有目录，来回切换会复用已安装角色。旧版雪球记录会在下一次成功保存时迁移，其他角色不会覆盖它。`selected_character` 与递增的 `selected_revision` 记录最后一次已验证的桌宠选择，当前活跃状态仍以 Pets 服务为准。

若需要同步到已配置的本机 Hub，设置技能在桌宠启用成功后可调用 `pet_setup.py sync --character <id>`，向固定的本机 `/v1/pet` 发送角色 ID。它会核对 Hub 的确认，但不将确认当作设备已经载入资源的证据；离线、尚未支持该端点或设备缺少资源时应明确说明。网页或设备自行切换，不会绕过 Pets 服务改变桌面宠物。

## 可选设备连接

不连接 ESP32 也能使用雪球 Pet。只有需要桌面圆屏上的实时会话状态时，才启动 companion 内的 Hub 和 BLE 服务。连接步骤、字段隐私、排序与心跳规则见 [设备状态说明](docs/DEVICE_STATUS.md)。当前会话名称属于用户授权的显示元数据，仅额外支持用户授权的显式提问标题，不发送答案、对话历史、代码或其他工具正文。

Pet 的动作触发与播放节奏由 ChatGPT/Codex 宿主控制；不能把 Pet 动画同步等同于圆屏设备的实时会话状态同步。

## 验证与复用

```sh
python3 tools/test_install_plugin.py
python3 tools/test_manifest_hooks.py
python3 tools/test_pet_setup.py
python3 plugins/xueqiu-status/scripts/pet_setup.py list
python3 plugins/xueqiu-status/scripts/pet_setup.py inspect --character snowball
```

`assets/pet/catalog.json` 索引角色，各角色 manifest 记录完整 v2 图集与哈希。`pet_setup.py` 检查资源、保存已通过 Pets 服务核对的本账号 Pet ID，并可向已配置的本机 Hub 通知选择；不调用私有 API、不直接修改宿主 Pet 数据库。

[官方插件打包与设置向导](https://developers.openai.com/plugins/build/plugins#add-an-onboarding-skill)

分享本目录的 ZIP 即可，不包含配对密钥、Hub token、打印机访问码或本机 Pet 安装状态。更新日期：2026-10-15。

角色声音资源随包存放在 `plugins/xueqiu-status/assets/audio/`，包含14角色×3状态原创拟声、测量与哈希清单、试听页和雪球v1历史文件。雪球当前采用柔软小狗v2声音。宿主Pets接口不支持音频扩展，资源随包不等同于桌宠自动播放声音；网页与原生端按各自音频播放器调用。

角色专属点击回话素材：`plugins/xueqiu-status/assets/dialogue/catalog.json`，包含14角色共168条原创短句及表情/动作。网页与ESP32使用共享源，ESP32从SD加载 `replies.json`；此素材附件不修改ChatGPT桌宠自身的对话行为。新原版音色尚待参考音频和试听。


## 电量、设备状态与续航日志（原生 0.11.0）

表盘下滑进入设备状态。新版桥接器可独立读取经过签名验证的电量遥测，在 AI 会话过期时仍记录供电和电量；校时不延长 AI 状态有效期。电脑端记录位于 `~/.local/state/xueqiu/device/`，网页本地预览状态页支持显示和下载。ESP32 SD 同时记录离线使用情况。V1 的电量/供电为估算，AXP2101 使用已有电量计；不会改写充电参数。完整协议与边界见 `POWER-STATUS.md`（原生源码中为 `docs/POWER-STATUS.md`）。


0.12.0授权范围更新：新增用户明确要求的「提问工具的问题标题」与独立问题ID，不含答案、任意工具输出或命令。支持按问题暂缓600秒、忽略和恢复；不执行回答或批准。正常空闲由在线Hub明确报告，运行状态失效仍保留未知。详见随包 ATTENTION-DETAILS.md。


0.13.0 用户授权更新：支持本机 Codex Desktop 普通异步问题的明确选项回答，网页还可输入文本；新增助手已公开的进度/结果摘要。旧文中“仅状态、不发送答案”指历史版本。提醒操作仍不等于回答；新回答接口须用户确认，不处理权限批准。详见 `ATTENTION-DETAILS.md`，私有 IPC 兼容性和实际验证边界分别记录。


语音输入（原生 0.15.2）：可选电脑端本地识别与设备录音已加入。初次启用运行 companion/setup-speech.py，使用独立 Python 环境和本地多语言模型。发送到已有本机 Codex 会话或对应待答问题之前必须确认识别文字。约 5.7 秒真人录音实测上传 2.236 秒、模型识别 2.468 秒。完整说明及验收边界见 [语音输入](docs/SPEECH-INPUT.md)。本轮更新本目录；外层既有 ZIP 未重新打包。
