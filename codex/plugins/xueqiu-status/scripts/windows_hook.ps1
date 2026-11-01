# Source for commandWindows. hooks.json contains this exact script encoded as
# UTF-16LE for PowerShell's -EncodedCommand, so no shell expands user paths.
# No execution-policy change, Python PATH dependency, service launch or pairing.
$ErrorActionPreference = 'Stop'
try {
    $root = Join-Path $env:LOCALAPPDATA 'SnowballCompanion'
    $marker = Join-Path $root 'current.json'
    if (!(Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'not_installed' }
    if ((Get-Item -LiteralPath $marker).Length -gt 4096) { throw 'invalid_marker' }
    $current = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($current.product -ne 'SnowballCompanion' -or $current.version -notmatch '^\d+\.\d+\.\d+$') { throw 'invalid_marker' }
    $cli = Join-Path $root ('app-' + $current.version + '\SnowballCLI.exe')
    $hook = Join-Path $env:PLUGIN_ROOT 'scripts\codex_hook.py'
    if (!(Test-Path -LiteralPath $cli -PathType Leaf) -or !(Test-Path -LiteralPath $hook -PathType Leaf)) { throw 'incomplete_install' }
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $payload = [Console]::In.ReadToEnd()
    $payload | & $cli hook --script $hook
} catch {
    [Console]::Error.WriteLine('Snowball Windows hook unavailable. Open Snowball Companion > Connection diagnostics.')
}
exit 0
