---
name: snowball-status
description: Check the local Snowball Companion Hub and explain Cursor status visibility.
---
Locate this plugin's root and run `python3 <plugin-root>/scripts/control.py doctor`.
Report only the diagnostic output. A reachable hub does not prove the device is
connected. Tell the user to choose Cursor in Snowball's settings. Do not start
services, change pets, reveal the token, or submit synthetic status events.
