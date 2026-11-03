---
name: setup
description: Set up, install, upgrade, inspect, or uninstall Snowball Companion; guide first device use through the bundled console, configure speech, or install and switch bundled ChatGPT Pets. Shared services and account Pets have separate removal scopes.
---

# Snowball Companion setup

The host exposes this skill as Setup, not a silent installation hook. Infer the
requested path from the user's context: physical Snowball, account Pet, or a
specific repair. Do not make a device owner complete Pet activation first. A
Pet-only request needs no hardware. Preserve existing authorization and progress.

## Physical device: one continuous guide

Read [first-use flow](references/first-use.md). **The computer console owns the
setup journey**: 准备电脑 → 连接雪球 → 确认同步 → 可选功能. The device owns physical
pairing confirmation and hands-on gesture practice. The AI chat owns plugin
installation, host Hook review and troubleshooting; it must not narrate a second
competing setup wizard.

Start by discovering the actual installed plugin, shared runtime and pairing
state. Do not overwrite a newer runtime or launch a duplicate Hub. For install,
upgrade or removal read [lifecycle](references/plugin-lifecycle.md) first.
A documentation request authorizes edits, not live installation/removal.

Open the real console with `python3 <plugin-root>/scripts/open_console.py`;
on Windows use `python -X utf8`. Resolve plugin-root as `../..` from this
SKILL.md directory. Read [console entry](references/local-console.md) if it
cannot open. Never guess a localhost port or direct a recipient to the developer
round-screen demo. Keep the complete package in a fixed folder.

Give one current action and destination: “现在看电脑控制台…”, “现在看雪球…”,
or “回到 Codex 会话…”. Say where to return. Prepare dependencies before the
2-minute pairing window. Use actual device button **开启配对** (older firmware:
开始配对), then return to console for binding. Do not instruct the user to
complete everything in OS Bluetooth Settings. Reuse valid existing bindings.

Leave Wi-Fi, speech and account Pets optional. After BLE and real status work,
let the user practice on the device. Tutorial success means a learned gesture,
not real authentication, message delivery or speech readiness. Resume the current
stage when interrupted instead of restarting setup.

## Repair only the missing capability

- Windows first binding, port discovery or an explicit complete-reset test:
  [Windows first run](references/windows-first-run.md). Prefer console; commands
  are fallbacks for diagnosed failures. Do not use Unix install/service scripts.
- Connected but no current status, Hook trust or launch problems:
  [real Hook loading](references/host-hooks.md). Approvals belong to the user;
  never write trusted_hash, inject fake events or create duplicate project Hooks.
- Windows shows only 稍后处理 / 忽略提醒, or cannot send/create:
  [Windows delivery](references/windows-send.md). Check the running Hub's channel,
  not only this tool process. Host handoff uses recipient-private endpoint state.
- Disabled recording or requested speech: [speech setup](references/speech-setup.md).
  Model/worker readiness and message delivery are separate checks.
- Windows ACL, service ownership or portability:
  [native compatibility](references/windows-compatibility.md).

Report verified layers separately: plugin loaded, real events, Hub, device
authentication, fresh status, optional answer/voice delivery. A Hub receipt does
not prove the Desktop question card dismissed. Do not send test chats or answers
without authorization. Do not interrupt active chats or firmware transfers to
reload documentation. On Windows do not claim independent idle proof/automatic
OTA eligibility from a healthy service alone.

## Account Pet or character switching

Read [Pet workflow](references/pets.md) only for requested desktop Pet setup,
listing, inspection or switching. It preserves asset validation, current-account
ownership, idempotent creation and authenticated selection. Use Snowball for an
unspecified first Pet. An explicit character switch authorizes selection without
another confirmation. Pending art is not installable.

Hardware and web selection cannot activate a desktop Pet through local files.
For requested all-surface switching, verify Pets selection before sending the
character ID to the Hub; verify device asset loading separately. Never package
recipient credentials, pairing keys, Pet IDs, host endpoints or session records.
