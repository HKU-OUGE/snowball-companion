# Bundled Snowball console

For opening device setup, connecting hardware or configuring Wi-Fi, use the
bundled local console. Do not send users to a developer's fixed localhost port.

Run `python3 <plugin-root>/scripts/open_console.py` (Windows: `python -X utf8`).
This opens the recipient's registered local console using an authenticated
discovery record. If it is missing, guide them to the full package's `codex/`
directory: Windows double-clicks `Open-Snowball.cmd`; Linux/macOS runs
`./open-console.sh`. Keep the package in a fixed directory. Do not invent a
working URL when no local service exists. The Windows installer creates a
desktop shortcut named 雪球控制台.

Follow [the first-use flow](first-use.md): 准备电脑 → 连接雪球 → 确认同步 → 可选功能.
The console is the main guide. Send the user to the device only for physical
confirmation or hands-on practice, and tell them when to return. Wi-Fi is optional
and must not come between BLE binding and the first real status check.
Prepare dependencies before asking the user to open the device pairing window.
An existing valid binding is reused, never silently overwritten. Wi-Fi secrets
should normally be entered in the console, not pasted into chat. Confirm the
device's own provisioning result rather than treating an accepted HTTP request
as saved credentials or successful Wi-Fi synchronization.

For Windows, the console supports the existing current-user login task and an
explicit, scoped private-LAN firewall helper. UAC remains a user decision. Never
approve the UAC prompt, disable the firewall, or start multiple bridge instances.
The console has no firmware flashing, eFuse programming or credential reset API.
Remote cloud service registration is separate from entering a remote URL.

Opening the console does not activate account Pets, approve Hooks or prove live
AI events. Preserve the existing lifecycle scopes and report each requested
capability separately. For Pet-only setup, keep using the Pets workflow.
