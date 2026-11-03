# Device updates

Snowball Companion downloads and verifies a release on the computer. While the
paired Bluetooth connection is available, the device receives that release
over Bluetooth. Wi-Fi is a fallback when Bluetooth is unavailable; it does not
compete with an established Bluetooth transfer.

## Incremental updates

The reliable delta foundation is firmware 0.28.2 or later. Older devices first
use the verified full-image path. Firmware with the delta capability reports its exact running image digest and
size through the authenticated connection. The companion prefers a separately
signed patch matching that image. The SD card holds the patch; the device checks
its complete digest before reconstructing the inactive application slot. The
result must match the full release signature, SHA-256, size, model, partition
layout and version. An incomplete patch never becomes a bootable image.

The previous application remains the rollback target until the replacement
passes its startup health check. Old firmware, a missing SD card, a missing
matching patch, or a rejected patch retains the verified full-image path.
Transient disconnection restarts the transfer negotiation instead of guessing a
sequence number. New firmware can reuse a complete patch or an eligible durable
prefix from SD, then checks the complete patch again.

Only release maintainers need a signing key or a patch generator. Recipient
plugins contain the public verification key. A Windows installation does not
need detools, a compiler, Unix utilities, or a private release key.

## Automatic background installation

Downloading and installing are separate operations. Downloading can proceed
while tasks run; live sessions and pending questions continue to be serviced,
including while the device reconstructs the application.

Automatic installation requires all of these conditions:

- The current application has passed its boot health check.
- The computer provides fresh, complete activity evidence. An expired heartbeat
  or an unknown session prevents installation.
- No monitored Codex session is active, waiting for an answer, or reporting an
  unresolved error.
- The main face has been idle for five continuous minutes. Touching the device,
  opening another page, or starting a task resets this interval.
- External power is connected **or** the valid battery reading is above 30%.

After a successful reboot, the companion checks again. It can select an
available intermediate signed patch, then repeat these checks for each next
version until it reaches the latest release. It does not skip the idle interval
or startup validation between versions. If an incremental link is missing, it
can use the verified complete release. A manual update stays pinned to the
version the user confirmed.

## Windows compatibility

The sender uses Bleak's Windows backend and its reported write capacity. Devices
that cannot use the continuous notification window fall back to acknowledged
writes. The faster PHY is negotiated by the device; older Bluetooth adapters can
retain the slower PHY. A faster mode is an optimization, not a pairing requirement.

File paths and caches support Unicode and spaces. Cache access uses the shared
Windows private-file implementation, rather than POSIX-only flags. Installer,
background-service, named-pipe and updater fixtures run in the repository's
Windows/Linux Python 3.12 and 3.13 CI matrix. These software tests do not establish
radio throughput on every Windows Bluetooth adapter.
