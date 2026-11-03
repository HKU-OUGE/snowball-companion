param(
    [string]$Python = 'python',
    [switch]$Device,
    [string]$Port,
    [switch]$AutoReconnect
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Use install.sh on Linux.' }
$codex = (Get-Command codex -ErrorAction Stop).Source
$pythonExe = (Get-Command $Python -ErrorAction Stop).Source
# Verify this host's commands rather than rewriting registration or Hook trust.
& $codex plugin marketplace list --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect installed Snowball plugins.' }
& $pythonExe -X utf8 (Join-Path $PSScriptRoot 'tools/install_plugin.py') --codex $codex --market-root $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Plugin installation failed; old Hook restoration was attempted.' }
& (Join-Path $PSScriptRoot 'tools/install_console_shortcut.ps1') -Python $pythonExe
if ($Device -or $AutoReconnect) {
    $venv = Join-Path $PSScriptRoot '.venv'
    if (-not (Test-Path (Join-Path $venv 'Scripts/python.exe'))) {
        & $pythonExe -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    $bridgePython = Join-Path $venv 'Scripts/python.exe'
    & $bridgePython -m pip install -r (Join-Path $PSScriptRoot 'companion/requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    $private = Join-Path $env:USERPROFILE '.config/xueqiu'
    New-Item -ItemType Directory -Path $private -Force | Out-Null
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $private /inheritance:r /grant:r ('*' + $sid + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict credential directory access.' }
    $pairing = Join-Path $private 'ble-pairing.json'
    if ($Device -and -not (Test-Path $pairing)) {
        if (-not $Port) { throw 'Connect a USB data cable, verify the device port, enable Start pairing on its connection page, then pass -Port COMx.' }
        & $bridgePython -X utf8 (Join-Path $PSScriptRoot 'companion/bridge.py') pair-usb --port $Port
        if ($LASTEXITCODE -ne 0) { throw 'USB binding failed. Enable Start pairing under Settings > Connect computer and retry within two minutes.' }
    }
    if ($AutoReconnect) {
        if (-not (Test-Path $pairing)) { throw 'Bind your device first with -Device -Port COMx.' }
        & (Join-Path $PSScriptRoot 'companion/windows-services.ps1') -Action Install -Python (Join-Path $venv 'Scripts/pythonw.exe')
    }
}
& $codex plugin list --marketplace xueqiu-local --json
Write-Output 'Open the Snowball Console desktop shortcut, or double-click Open-Snowball.cmd in this folder. The bundled wizard prepares dependencies, binds USB/Bluetooth, configures Wi-Fi and checks the real connection.'
Write-Output 'Plugin registered. Setup/trust is not per-chat. Use the current chat composer Review hooks button. For the global Settings list, add this fixed repository once through File > Open folder; an empty no-project list does not imply missing installation. Verify real events after user review. Review changed definitions and run Setup to verify the account Pet; hardware connection is checked separately.'
