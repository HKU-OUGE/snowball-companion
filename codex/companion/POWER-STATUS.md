# Device status, battery monitoring and runtime logs (0.11.0)

Swipe down from the face to Device Status; swipe up or right to return. Existing upward Sessions and leftward Settings gestures are unchanged. The page separates Bluetooth connection from freshness of AI task information, so an expired task can show “等待新消息” while Bluetooth remains connected.

The monitor samples every five seconds. AXP2101 is detected by I2C identity, then existing status, fuel-gauge and voltage registers are read. It does not invoke the BSP's charge/rail initializer or change charge voltage, current, termination or power rails. A V1 board uses its GPIO1 divider with calibrated ADC where available; voltage-based percentage and external-power detection are marked as estimates. External supply is distinct from confirmed active charging. Missing or stale sensor data remains unknown.

The new encrypted GATT characteristic `9e7a0004-6ed4-4b6b-bf37-43dc42838c20` returns a compact, HMAC-authenticated snapshot bound to the current device nonce. Its long-read response is frozen across ATT fragments. Opcode 5 on the existing authenticated write channel synchronizes UTC; it never updates the AI task lease. Existing status/catalog/pet payloads are unchanged. An older computer Bluetooth service cache may need rediscovery when first upgrading; the installed companion uses the same application pairing identity.

## Records

- Device SD: `/snowball/logs/power-<boot-id>-<part>.jsonl` (the firmware mounts the card at `/sdcard`).
- Computer: `~/.local/state/xueqiu/device/power-YYYY-MM-DD.jsonl`, grouped by UTC date. `latest.json` is the current authenticated snapshot.
- Local webpage: Device Status shows the live sample and offers a download of the latest seven days of computer logs. Static/standalone copies provide a separately labelled demonstration. AI sessions elsewhere in the web concept remain simulated.

Each device record includes UTC epoch seconds, a clock-synchronized flag, boot identity and uptime, voltage, percentage, supply/charging mode, measurement quality, awake/asleep durations and elapsed battery use. Before the first clock synchronization, timestamp is 0 and `clock_synced` is false: uptime still orders the records, without inventing a date. Computer rows also preserve receiver UTC/ISO time separately. The webpage displays receiver time in the user's local timezone.

SD records are written every minute and on supply/time-sync changes, from a low-priority worker using the shared SD lock. Resource loading and audio retain the lock when needed; logging retries later. Files roll over at 1 MiB without deleting older records. Logging failures are counted and shown, and do not stop BLE status delivery. The computer polls telemetry every ten seconds and records a minute sample plus power/boot/validity changes. SD offline history remains on the card; the webpage download contains computer-received records, not an automatic import of offline SD history.

`battery_s` is elapsed battery-powered use in the current segment. `start_kind=1` means an observed transition away from external power; `start_kind=2` means booted on battery and is a lower bound. Neither is a measurement of remaining runtime or proof of a full-charge cycle. No estimated remaining hours are displayed without a measured charge/discharge history.

## Conservative energy changes

The IMU hardware configuration and visible animation timing stay unchanged. IMU register reads run at the original 4 ms period in Play/tutorial Play and at 100 ms elsewhere. UI housekeeping runs at 33 ms for visible animations, Play, tutorial/boot and animated sleep signals; static pages/black sleep use 100 ms. LVGL's separate input/render/transition timers remain active. Colour breathing (1.5 s), flashing (1 s), display brightness, voice, touch wake and BLE remain enabled with their existing settings. No deep sleep or CPU-frequency change is enabled in this release.

These changes reduce unnecessary polling; they are not a measured percentage of whole-device power savings. Current and full-charge endurance require hardware measurement/a logged normal-use cycle.

## Diagnostics

USB `:POWER_STATUS` reports `XQPOWER` and `XQENERGY` (IMU read counter and UI tick period). It does not alter supply state, settings or AI session state. The new payload is described in `main/power_monitor.c` and decoded/validated in `desktop/power_telemetry.py`. An invalid/missing telemetry read does not prevent normal AI status updates.
