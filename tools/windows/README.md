# Windows native candidate build

Build on Windows x64 with CPython 3.12.10. The pinned dependency file includes the
runtime, build toolchain and the explicit Windows test suite. No device access,
plugin installation, services or release publication is performed by the build.

```powershell
python -m venv .venv-windows
.venv-windows\Scripts\python.exe -m pip install --only-binary=:all: -r tools/windows/requirements-build.txt
.\tools\windows\build.ps1 -Python .venv-windows\Scripts\python.exe
.venv-windows\Scripts\python.exe tools/windows/smoke_distribution.py dist/windows/Snowball
```

`dist/windows/Snowball` contains `Snowball.exe` (GUI), `SnowballCLI.exe` (console)
one shared `_internal` runtime, and `WINDOWS-BUNDLE.json`. The native manifest
covers every final file except itself, with exact sizes and SHA-256 hashes; the
installer rejects extra or changed files. Keep the entire directory together.
PyInstaller analyzes the same `codex/companion/snowball_windows.py` entry point
for both executables. Default GUI/CLI dispatch is owned by that entry point.

Inside a frozen process, `Path(sys._MEIPASS) / 'bundled' / 'codex'` is the Codex
project directory. Its sibling `docs/`, `README.md`, `BUNDLE.json` and
`FILES.sha256.json` live in `Path(sys._MEIPASS) / 'bundled'`. Runtime and installer
code must not derive those paths from the current working directory.

The payload comes only from `bundle_inputs(root, 'codex')` in the existing ZIP
builder. It uses `plugin_bundle_files.json`, refuses missing required Windows
sources, and checks every staged file digest after freezing. No repository-wide
copy, home-directory discovery, credentials, firmware, web or cloud build tree
is included. Dependencies are collected from the isolated pinned environment.
The installer may copy the complete Codex source bundle; account authorization
and Bluetooth pairing remain recipient actions.

The workflow runs exactly the files in `ci_tests.json`, including Hook manifest
and semantic replay tests. `test_windows_installer.py` exercises the production
installer with real file/ACL operations and a fake external CLI; the older
`test_install_plugin.py` remains a Linux bash/SIGTERM regression and is not
misrepresented as Windows validation. It uses a disposable
standard local user on the hosted Windows runner, without administrator group
membership or relaxed credential ACL rules. If standard-user process creation
or ACL validation fails, the job fails. Temporary account passwords are random,
masked and never written to the artifact. This wrapper is CI-only, not part of
the distributed installer.

After tests, the workflow checks the actual PE architecture and subsystem,
`SnowballCLI.exe --version`, `doctor --json`, GUI `gui --smoke-test`, and six
synthetic Hook events using an isolated temporary home. It calls the production
`install_bundle` to copy the actual build into a temporary `LOCALAPPDATA` path
containing Chinese characters and spaces, then runs the installed CLI and the
actual `commandWindows` PowerShell launcher from a simulated plugin cache. Python
is removed from the child PATH for that launcher check. It does not call shortcut
creation, plugin registration, service startup or pairing. No real session,
token, Hub service or device is used. Only a successful native run produces the ZIP and
SHA-256 candidate artifacts. A green build does not prove Bluetooth hardware
connectivity, real host Hook delivery, or a signed public release.
