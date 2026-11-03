# Web-owned automatic setup

When the user invokes Setup for first use or asks to configure Snowball, start the
requested automatic setup without requiring separate chat requests for speech,
Pet or login startup. The console is the sole configuration/progress surface.
Read-only inspection, Pet-only switching, uninstall and simple page opening retain
their narrower scope; do not silently start a full installation for those requests.

## Open and start

Resolve `<plugin-root>` from SKILL.md and run:

```sh
python3 <plugin-root>/scripts/open_console.py --setup
```

On Windows use `python -X utf8`, never Unix service commands. The command opens
the recipient console and starts an idempotent private setup plan. The browser
shows dependency preparation, current-user login startup, background connection,
local speech and account Pet status. Existing services, models and bindings are
checked and reused. No firmware writes, reset, Wi-Fi password collection in chat,
Hook trust changes or automatic test-message sends belong to this plan.

**Do not stop after opening the page.** Continue the account Pet work below, then
monitor the same plan until all requested tasks succeed, the user skips them, or a
specific external action is required. Keep progress and any failure in the webpage.
The private launcher grant stays in recipient state; never print/copy it.

```sh
python3 <plugin-root>/scripts/open_console.py --setup-status
python3 <plugin-root>/scripts/open_console.py --setup-watch
```

`--setup-watch` waits at most 25 seconds and keeps the agent connection alive.
Poll between meaningful operations rather than issuing duplicate installers. When
local work takes longer, continue bounded waits and explain progress briefly. A
closed/reopened page resumes the plan; a restarted service marks interrupted work
for recheck rather than claiming success.

## Account Pet lane

The console intentionally cannot install an account Pet by writing preferences or
forging a success receipt. When the plan's Pet state is pending, read
[pets.md](pets.md) and perform its real authenticated Pets workflow.

- The webpage's `character=auto` means retain an already active account Pet after
  verifying it in the current account; if none is active, install/select Snowball.
  A specific character selected on the webpage authorizes that selection.
- Before calling Pets tools, report `--pet-state working --character <id>` using
  the same `scripts/open_console.py`. For auto retention, `<id>` may be `auto`;
  for a new Snowball install use `snowball`.
- Validate/create/select or verify the existing active Pet through the host tools.
  Keep ownership checks, asset checks, exact saved receipts and bounded retries.
  Only after actual success report `--pet-state verified --character <id>`.
- Missing Pets authentication: report `--pet-state needs_auth --character <id>`
  and use the host's connection flow. The web page explains the required host
  authorization and retains the rest of the setup. Do not rewrite host credentials
  or approve on behalf of the user. After authorization and a web retry, continue
  the same account verification; do not restart all configuration.
- An unresolved tool result or failure gets `--pet-state failed`; never send a
  verified receipt just because a PNG or a local Pet receipt exists.

The browser can select a character, request retry or skip. It cannot submit the
privileged verification receipt. A stale run/grant or a changed selection is
rejected. Recheck status before any account mutation and preserve unknown-create
outcomes to avoid duplicate Pets. Other adapters mark this account-Pet capability
not applicable; do not claim it was installed in Claude or Cursor.

## Speech and login startup

New users default to the multilingual Base model (about 148 MB model download).
The webpage also offers Tiny (about 78 MB) and Small (about 486 MB). These numbers
exclude Python dependencies. Existing valid models are retained in auto mode,
including Small. Model choice is the user's web control; do not downgrade a
working model just to make setup finish faster. See [speech setup](speech-setup.md).
Model download, successful loading and authenticated service readiness are separate
stages. A ready worker still needs a real device recording to establish acoustic
and delivery behavior. Do not record or submit a transcript automatically.

Windows uses the current-user scheduled task; Linux uses owned systemd user units.
Check actual registration, not a successful command alone. Reuse existing valid
Snowball startup and preserve another installation's runtime; resolve ownership
conflicts instead of replacing unrelated services. macOS automatic registration is
currently reported unsupported, rather than invented as successful.

Physical USB connection and firmware-required device confirmation cannot be done
by pretending the user touched the screen. Keep instructions in the webpage;
the user only visits the device to perform the specified physical action. Wi-Fi
name/password are entered in the webpage. No second chat checklist is required.

## Offline resources and interpreter fallback

Mounted SD resources are preferred to network downloads. `offline/wheels/` has separate hash-pinned manifests for the actual OS, CPU and Python minor version; do not substitute another platform or skip validation. Tiny/Base/Small model files are shared across systems. Dependencies and models are copied into private PC caches before installation/use.

If dependency preparation fails because the system interpreter lacks venv/ensurepip, use the verified SD archive via `companion/offline_resources.py --prepare-python`. It prints an owned interpreter path, not a credential. Use that interpreter to finish the owned environment preparation and retry the same webpage step; preserve unrelated Python environments. Do not ask the user to run a second command tutorial. A matching portable Python archive does not supply OS Bluetooth drivers/BlueZ or AI account authorization.
