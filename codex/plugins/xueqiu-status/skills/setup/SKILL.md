---
name: setup
description: Set up, install, upgrade, inspect, or uninstall Snowball Companion; guide first device use through the bundled console, configure speech, or install and switch bundled ChatGPT Pets. Shared services and account Pets have separate removal scopes.
---

# Snowball Companion setup

The host exposes this skill as Setup, not a silent installation hook. Infer the
requested path from the user's context: physical Snowball, account Pet, or a
specific repair. Do not make a device owner complete Pet activation first. A
Pet-only request needs no hardware. Preserve existing authorization and progress.

## Standalone installer users

Users can install from the v0.7.0 native installer without this skill. The installer
opens `/install`, lets the user select features, and reports progress in the same
local console. Do not start the agent's automatic plan in parallel with an active
installer plan or silently re-enable unchecked speech/autostart options. Respect
its saved choices. Account Pet operations still require the authenticated host.
System privileges are requested by UAC/polkit, never a webpage password field.

## First use: configure everything in the webpage

Read [automatic setup](references/automatic-setup.md). For a Setup/configuration
request, run `python3 <plugin-root>/scripts/open_console.py --setup` (Windows:
`python -X utf8`) and continue the work after the browser opens. Resolve plugin-root
as `../..` from this SKILL.md directory. The flag starts environment preparation,
login startup, connection services and speech; the agent performs real account
Pet operations and reports their results to the same webpage. Do not require
separate “configure speech / install Pet / enable startup” chat requests.

All configuration choices, progress, retry and skip controls stay in the console.
Use `--setup-status` / `--setup-watch` to follow the same plan and the documented
`--pet-state` receipts after actual Pets tool results. Opening the webpage is not
completion. Keep the agent connected until requested work is verified or a
specific host/device action is needed, and show that need in the webpage.

Start by discovering the installed plugin and shared runtime. For install,
upgrade or removal read [lifecycle](references/plugin-lifecycle.md). Do not
replace a newer runtime, restart active chats, duplicate services or reset
pairing. A documentation request authorizes edits, not live installation.
For read-only diagnosis or only opening an existing settings page, omit `--setup`.

Use [console entry](references/local-console.md) for a missing launcher and
[first-use flow](references/first-use.md) for the device handoff. On firmware0.27.0 and later, first-use pairing is opened by the installer
through USB while the device is at its connection tutorial; the user only connects
the cable. Reuse valid bindings. Always attempt the real USB binding before asking
for a device tap. Only `usb_pairing_confirmation_required` (firmware
`open_connection_page`) warrants the real 开启配对 confirmation on older/configured
devices; no response, startup delay, or serial access failures do not. Do not
preemptively follow an older README that asks for a tap. Device tutorial is a gesture exercise, not another configuration
wizard. Never send Wi-Fi passwords to chat or use a developer-only localhost URL.

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

Read [Pet workflow](references/pets.md) for the automatic setup Pet lane or requested desktop Pet setup,
listing, inspection or switching. It preserves asset validation, current-account
ownership, idempotent creation and authenticated selection. Use Snowball for an
unspecified first Pet. An explicit character switch authorizes selection without
another confirmation. Pending art is not installable.

Hardware and web selection cannot activate a desktop Pet through local files.
For requested all-surface switching, verify Pets selection before sending the
character ID to the Hub; verify device asset loading separately. Never package
recipient credentials, pairing keys, Pet IDs, host endpoints or session records.
