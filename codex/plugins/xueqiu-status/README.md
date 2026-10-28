# Snowball Companion

Use **Setup** to install or switch a desktop Pet. Ask “列出可用宠物” or “切换到边牧”; setup checks the character catalog, validates its complete sprite sheet, reuses an existing owned Pet when possible, then activates it through the authenticated Pets service.

The library defines fourteen characters: Snowball, Border Collie, round-faced Orange Cat, blue-eyed Ragdoll, Chiikawa, Hachiware, Usagi, My Melody, Kuromi, Homelander, **old Joseph Joestar from Stardust Crusaders**, StellaLou, Frank Underwood, and Heisenberg. The source of availability is `assets/pet/catalog.json`: pending artwork is visible in the catalog but cannot be installed. Check actual availability with `python3 scripts/pet_setup.py list` rather than assuming all fourteen atlases are bundled.

This is guided post-install setup, not a silent account mutation during package installation. Pets service availability and host authorization are required. The desktop Pet works without a hardware device. Each recipient owns their Pet copies; no distributor account IDs are bundled.

Local receipts use a per-character map in `$CODEX_HOME/state/plugins/xueqiu-status/pet-install.json`. Version 0.3 Snowball receipts migrate on the next successful record. Record ownership separately from activation: `record --character <id> --pet-id <id>` preserves an owned installation, while `--selected` is only for a confirmed active desktop Pet. Switching preserves the other characters for reuse.

The plugin also includes optional local AI-status hooks for a Bluetooth companion display. These send status, authorized session names and explicitly posted question titles for waiting details. Answers, arbitrary conversation/tool bodies and execution commands are excluded. Reminder controls only snooze, ignore or restore a specific notification. Review hooks through the host when required. The full marketplace bundle contains companion bridge tools and device instructions. Desktop activation requires Pets MCP; a local selected-character value alone does not activate it.

The product name is **Snowball Companion**; the stable plugin identifier remains `xueqiu-status` for upgrade compatibility.

## Reusable character audio

`assets/audio/` contains original procedural cues for all14characters: `complete.wav`, `waiting.wav`, and `error.wav` in each character directory, with signal measurements and SHA256 in `manifest.json`. Snowball uses the softer v2 cues; its original v1 files remain under `source-history/snowball-v1/`. Open `assets/audio/preview.html` to audition. These are synthesized approximations, not actor recordings or cloned voices; listening validation is still pending.

The desktop Pets API has no supported audio extension: bundling these WAVs makes them reusable by the web/native companion, **it does not make the host desktop Pet play sounds automatically**.
