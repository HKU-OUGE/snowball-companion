# Snowball Companion maintenance

This repository contains three separate adapters for one product: `codex/`,
`claude/`, and `cursor/`. Preserve the user's chosen platform and scope.

## Installation and removal

Read [docs/INSTALLATION.md](docs/INSTALLATION.md) before installing, upgrading,
disabling, uninstalling, or cleaning up an adapter. A documentation request is
not a request to install or uninstall anything on the current machine.

- Discover the actual installation, CLI capabilities, installation scope, and
  shared service paths before changing them. Do not assume this checkout is the
  active runtime. Do not overwrite a newer shared runtime with bundled files.
- Codex identity: `xueqiu-status@xueqiu-local`. Claude identity:
  `snowball-companion@snowball-local`. Cursor local directory:
  `~/.cursor/plugins/local/snowball-companion/`.
- Prefer the host's plugin manager. For Codex, use the native uninstall tool
  when available and explicitly requested; otherwise use the verified CLI.
  Do not delete Codex caches or rewrite plugin trust as a substitute.
- Default uninstall scope is the requested adapter only. Hub, BLE/Wi-Fi/cloud
  services, pairing state, and account Pets have separate lifecycles. Removing
  one adapter must not silently disable another adapter's shared services.
- Inspect service `ExecStart` and foreground process arguments before removing
  a directory: a bundled `runtime/` or `companion/` may still be in use.
- Preserve credentials, OTA signing keys, encrypted backups, firmware and SD
  resources during ordinary uninstall. Complete data erasure requires explicit
  scope; identify exact owned paths rather than deleting an entire app profile.
- Do not restart a host with active chats or interrupt a firmware transfer just
  to reload documentation or a plugin. Explain any genuinely required reload.

## Validation and delivery

Verify package registration/loading, real hook delivery, Hub health, and device
connectivity separately. A healthy Hub or a passing synthetic test is not a real
Agent-to-device test. Do not send a test conversation on the user's behalf
without authorization.

Use isolated test homes or fixtures for destructive lifecycle tests. Do not
uninstall the user's live plugin to test documentation. Update the applicable
platform README and bundled skill reference when lifecycle instructions change.

Public source must not contain recipient credentials, real device/account
identifiers, local session records, absolute developer-machine paths, or private
release keys. A source edit does not authorize a Git push, cloud deployment,
firmware installation, or public release.
