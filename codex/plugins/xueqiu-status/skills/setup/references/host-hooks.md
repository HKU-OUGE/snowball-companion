# Real host event loading and Windows launch checks

状态待同步 / Awaiting status: a verified BLE connection is separate from live
Codex events. Check the actual Hooks list and private adapter record timestamps.
A chat created before plugin installation may need a new chat or host reload
when no active work is interrupted. A fresh configuration inspection is not
proof that an existing chat loaded it. If no real events arrive, report pending
status verification; never inject fake busy/idle events to make the screen green.


## One-time setup versus live host loading

User-scope plugin installation and trust of an unchanged definition are not
per-chat setup. Do not ask users to configure Hooks again for every chat.
Inspect the current chat's actual Hook configuration separately from a fresh
CLI app-server's hooks/list response. The current Desktop Settings > Hooks
page disables its query when there are no configured project roots, showing
an empty list and disabled refresh. That UI state is not proof that the host
failed to load Hooks; do not prescribe repeated restarts or per-chat setup.
Use the chat composer's Review hooks button to open the current definitions,
inspect Details, and let the user approve them. A modified definition is skipped
until reviewed. Never auto-approve, write trusted_hash, or add duplicate project
Hooks. Prefer the composer entry for ordinary setup. If the user specifically wants
the global Settings list, add the existing fixed-location repository once via
File > Open folder, then verify Settings > Hooks lists Snowball Companion and
the actual pending count. This works around the no-project-roots query issue;
do not create a project per chat, change app installation resources, or approve
the definitions on the user's behalf. Verify real delivery after the user reviews once. Only
investigate a reload when the actual configuration is missing, and never
interrupt active work to restart the host.
Cloud orchestration does not support local/plugin command Hooks even when tools
execute locally; check the actual mode before attributing missing events to
trust or installing duplicate project Hooks. See the official Hooks docs at
https://learn.chatgpt.com/docs/hooks.


## Windows native launch verification

Use configure_windows_plugin.py to generate commandWindows, preserving the
portable command. Raw quoted executable strings can fail through cmd /C and
PowerShell even if invoking Python directly succeeds. Verify the configured
command through both native shells with isolated stdin/state before requesting
review. Path quoting regressions must cover spaces, Unicode and apostrophes.
Never interpolate Hook payloads into the launch command. A launch-command
change requires a fresh user review of that exact definition; never rewrite
trusted_hash or approve on the user's behalf. Keep synthetic tests isolated
from the live Hub and verify actual chat delivery separately after review.
