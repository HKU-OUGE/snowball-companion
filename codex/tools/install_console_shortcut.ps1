param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Destination = [Environment]::GetFolderPath('DesktopDirectory')
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
$root = Split-Path $PSScriptRoot
$script = Join-Path $root 'Open-Snowball.ps1'
if (-not (Test-Path -LiteralPath $script) -or -not (Test-Path -LiteralPath $Python)) { throw 'Console launcher or Python is missing.' }
if (-not (Test-Path -LiteralPath $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
$name = ([char]0x96EA).ToString() + ([char]0x7403).ToString() + ([char]0x63A7).ToString() + ([char]0x5236).ToString() + ([char]0x53F0).ToString() + '.lnk'
$path = Join-Path $Destination $name
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
$marker = 'Snowball Companion local console'
if ((Test-Path -LiteralPath $path) -and $shortcut.Description -ne $marker) {
    Write-Warning 'An unrelated desktop shortcut has the same name. It was preserved; use Open-Snowball.cmd instead.'
    return
}
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $script + '" -Python "' + $Python + '"'
$shortcut.WorkingDirectory = $root
$shortcut.Description = $marker
$shortcut.Save()
Write-Output ('Snowball Console shortcut created: ' + $path)
