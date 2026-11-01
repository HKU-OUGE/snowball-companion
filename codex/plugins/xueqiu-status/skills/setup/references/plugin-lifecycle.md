# Codex plugin installation, upgrade and removal

Use this reference only for managing the Snowball Companion software package.
The stable plugin ID is `xueqiu-status@xueqiu-local`; the display name is Snowball
Companion. An account Pet, the local plugin, and hardware services are separate.
Writing instructions is not authorization to execute lifecycle changes.

## Discover the actual installation

Use available host inspection tools or these read-only CLI commands:

```sh
codex plugin list --json
codex plugin marketplace list --json
codex plugin remove --help
```

Resolve the executable from the current host. If `codex` is absent from PATH,
the source installer checks `/usr/lib/chatgpt/resources/codex` on supported
Linux packaging; do not assume that location on other systems.

Resolve this plugin root as `../..` relative to the directory containing
`SKILL.md`. A cached plugin root is NOT the complete marketplace: do not assume
an `install.sh` exists above it. Obtain the source location from marketplace
inspection or the user's package. Check its README, manifest and installer.

Inspect shared service `ExecStart` and foreground process arguments before
removing a source directory. Existing services may run from a different or newer
runtime. Do not print credential contents or overwrite shared services with an
older bundled runtime.

## Install or upgrade when requested

On native Windows, use the installed Snowball Companion wizard and its bundled
`SnowballCLI.exe`; see [WINDOWS.md](WINDOWS.md). Do not run `install.sh` or
require the user to install Python or invoke a chat for every connection.
The wizard separates registration, Hook trust, first binding, and background
startup; USB is only required for first binding or rebinding.

On Linux, prefer the supplied marketplace-root `install.sh`. It delegates registration
to Codex and preserves missing historical Hook scripts used by existing chats.
It does not silently create an account Pet, modify Hook trust, or configure a
device. Use the Setup character workflow only when the user requests the Pet.

If the installer is unavailable, inspect the current host's official plugin
manager rather than inventing commands or deleting caches. Confirm the exact
plugin appears installed/enabled. If the host requests Hook review, use its
normal flow; never auto-approve changes. New or reloaded sessions verify loading;
do not interrupt active chats merely to reload a plugin.

## Remove when requested

If the host exposes a plugin-uninstall tool, prefer it with the exact plugin ID.
The user's explicit uninstall request is authorization; do not ask again unless
the target or scope is genuinely ambiguous. If that tool is unavailable and the
verified CLI supports it:

```sh
codex plugin remove xueqiu-status@xueqiu-local --json
codex plugin list --json
```

Verify removal from registration and loading in a new/reloaded session. Old
chats can retain stale Hook references; finish or reload those chats as needed,
without reinstalling the plugin to mask the removal. Do not directly delete
Codex cache/config trees or alter trust records as a substitute for uninstall.

Remove the `xueqiu-local` marketplace only if requested and it has no other
plugins the user wants to keep:

```sh
codex plugin marketplace remove xueqiu-local --json
```

Ordinary removal retains the user's account Pets, other app connections, pairing
credentials, session preferences, device firmware and SD resources. Deleting
an account Pet requires the authenticated Pets service and verified ownership;
deleting its local receipt does not delete the Pet.

## Entire-product removal

Stop shared Hub/BLE/Wi-Fi/cloud services only when the user also requests removal
of the entire companion service. Codex, Claude and Cursor may share them. On
Linux, inspect existing owned `snowball-hub`, `snowball-bridge`,
`snowball-network`, and `snowball-cloud` user units; stop/disable only the units
actually present and owned by this installation. Use the existing manager for
foreground or non-systemd installations. Do not use broad process kills.

Default data retention includes `~/.config/xueqiu/` (credentials and keys),
`~/.local/state/xueqiu/` (state/logs/backups), and independent runtime directories.
Honor `CODEX_HOME` and XDG overrides; identify real paths before cleanup. Full
erasure needs an explicit data scope, not an inferred consequence of uninstall.
Do not erase firmware, eFuse, SD cards, cloud resources or recovery/signing keys.

Report package removal, service state, retained data and Pet state separately.
Test destructive lifecycle behavior in isolated homes/fixtures, never by
uninstalling the user's live plugin to validate documentation.

Command reference checked 2026-11-01:
[Codex developer commands](https://learn.chatgpt.com/docs/developer-commands),
[plugin packaging](https://developers.openai.com/plugins/build/plugins).
