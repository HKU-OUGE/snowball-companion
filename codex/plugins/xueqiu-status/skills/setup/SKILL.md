---
name: setup
description: Set up, install, upgrade, inspect, or uninstall the Snowball Companion Codex plugin; list, install, or switch its bundled ChatGPT Pets. Shared device services and account Pets have separate removal scopes.
---

# Snowball Companion setup and character switching

Use this workflow when the user runs Setup or asks to list, install, or switch a companion. The host exposes this skill as the plugin setup action; it is not a silent installation hook. The user's explicit switch request authorizes selecting that character; do not ask for the same permission again.

## Software installation and removal

For plugin package installation, upgrade, inspection, uninstall, or cleanup,
read [plugin lifecycle](references/plugin-lifecycle.md). Use its discovery and
scope checks before changing a host installation. A documentation-only request
must not perform installation or removal. If the user names only a Pet, keep
that request in the authenticated Pets workflow instead of removing software.

For Pet setup or switching, continue below. Hardware services remain optional.

## Choose a character

1. Resolve the plugin root as `../..` relative to the directory containing this SKILL.md. Run `python3 <plugin-root>/scripts/pet_setup.py list`. This lists stable character IDs, names, and actual installability. For an unspecified first setup use `snowball`. If the user asks to browse, show the catalog and distinguish pending artwork from installable characters. If the user asks for a specific character, map its name to the catalog ID. Joseph Joestar is **old Joseph from Stardust Crusaders**, not young Joseph. Never claim a pending portrait is an installable animated Pet.
2. Run `python3 <plugin-root>/scripts/pet_setup.py inspect --character <id>`. A ready asset has its hash, v2 dimensions, and animation-row contract checked. If the asset is absent or invalid, stop this character's installation and report the real availability. Do not silently substitute Snowball, regenerate art, fabricate animation rows, or change the active Pet. Use the absolute bundled PNG path and metadata returned by this command.

## Install or switch the selected Pet

3. Use the registered ChatGPT Pets tools (`list_pets`, `validate_pet_spritesheet`, `prepare_pet_upload`, `create_pet`, `select_pet`). The required service is declared in `.app.json`. If unavailable, tell the user to complete the host's Pets connection flow and rerun setup. Do not use private APIs, scrape authentication, or write the host's Pet library/configuration directly.
4. List all the current account's pets, following cursors. An unambiguous ID for **this character** already established by the user in this conversation may be reused after ownership verification. Otherwise reuse this character's saved receipt only when its asset key/hash match the bundled asset and the ID is a custom Pet owned by the current account. Old single-Pet receipts automatically migrate to Snowball; do not bind them to another character. A stale receipt from a different account is never proof of ownership.
5. If no receipt matches, look for exactly one custom Pet whose description contains the exact `[Snowball Companion: <hash-prefix>]` marker from this character's bundled description. Multiple marked matches require a choice. Same-name unmarked Pets may be older or unrelated: show the candidate and ask whether to reuse it before binding. Never update or delete another Pet to avoid a collision.
6. For a status-only request, report installed/active/missing after ownership verification and stop without creating, recording, or selecting. For installation/switching, create only if no existing Pet matches. Call `validate_pet_spritesheet` on the exact bundled PNG and require `valid=true`; then `prepare_pet_upload` with the same file and `create_pet` with its upload session and bundled name/description. Each recipient gets an owned copy; never embed the distributor's Pet ID or expiring image URL. On a transient/unknown create result, retry with the same upload session and metadata, at most twice. Never prepare a fresh upload blindly after an unknown creation result. Stop and report unresolved outcomes.
7. Call `select_pet` for the selected/created Pet and verify the returned active ID. Only then run `python3 <plugin-root>/scripts/pet_setup.py record --character <id> --pet-id <verified-id> --selected`. If a Pet was created but activation failed, you may record the verified owned ID without `--selected` so retrying does not create another copy. Report the actual result; do not claim activation after a failed call.

## Companion synchronization boundary

The product is **Snowball Companion**; Snowball is one character in its library. Private receipt state remembers the last verified desktop selection, not the host's live state. Always recheck the Pets service for current activation. Host applications control desktop animation triggers/timing.

A web or ESP32 selection alone cannot activate the desktop Pet through a filesystem preference: activation must use authenticated `select_pet`. When the user requests all surfaces, run `python3 <plugin-root>/scripts/pet_setup.py sync --character <id>` after successful desktop selection. It sends only the character ID to the authenticated local Hub at `/v1/pet` and verifies the returned ID/revision. A Hub acknowledgement does not prove ESP32 asset loading; verify the device response separately. If that tool or the hardware is unavailable, report desktop and device results separately; do not claim cross-device synchronization. Do not invent an endpoint or write the host Pet database to simulate it.

## Optional hardware companion

The Pet works without an ESP32 or Bluetooth device. Configure the local status Hub and BLE bridge only when requested; use the package README and existing companion tools. Preserve pairing, session priorities, and hook trust. Do not start duplicate services, auto-approve hooks, restart the host, or send conversation/tool bodies.

Recipient state belongs only under the recipient's CODEX_HOME state directory, never plugin assets. Never package credentials, pairing keys, account IDs, or local session records. A new character becomes available only after its full v2 atlas and manifest have passed the artwork pipeline and the catalog is updated to ready; account installation still requires the live Pets validator.
