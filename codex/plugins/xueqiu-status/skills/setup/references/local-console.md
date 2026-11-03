# Bundled local console

For device setup use the installed package's authenticated local launcher, not a fixed developer port. Explicit setup requests use `python3 <plugin-root>/scripts/open_console.py --setup` (Windows: `python -X utf8`), then follow [automatic-setup.md](automatic-setup.md). Keep observing and carrying out the account Pet lane; opening the page alone is not completion.

For inspection or merely opening the existing console omit `--setup`; it does not start installation. If the entry is missing, resolve the complete package: `codex/` for Codex; `console/` for Claude/Cursor. Windows double-clicks `Open-Snowball.cmd`, Linux/macOS runs `./open-console.sh`. Keep the package in a fixed PC directory. Do not invent a working URL.

All choices and progress are in the webpage: automatic setup → connection → verify sync → optional network. Direct the user to the physical device only when its pairing confirmation or subsequent gesture practice requires it; say when to return. Passwords stay in the local webpage. Existing identity and services are reused, never silently overwritten.

Windows uses the current-user login task and a separate explicit private-LAN firewall helper; UAC remains the user's decision. Linux checks owned systemd user services, macOS autostart is explicitly unsupported. The webpage has no firmware flashing, eFuse or credential reset API. Account Pet verification uses the agent's real Pets tools and private receipts, never a browser success button. Preserve separate lifecycle scopes.
