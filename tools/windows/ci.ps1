param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'CI must use a standard user token; ACL checks are not bypassed' }
$BuildProfile = [Environment]::GetFolderPath('UserProfile')
if (-not $BuildProfile) { throw 'Standard user profile was not loaded' }
$env:USERPROFILE = $BuildProfile
$env:HOME = $BuildProfile
$env:APPDATA = Join-Path $BuildProfile 'AppData/Roaming'
$env:LOCALAPPDATA = Join-Path $BuildProfile 'AppData/Local'
$env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:CODEX_HOME = Join-Path $BuildProfile '.codex'
$env:XDG_CONFIG_HOME = Join-Path $BuildProfile '.config'
$env:XDG_STATE_HOME = Join-Path $BuildProfile '.local/state'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
Set-Location $Repo
$Venv = Join-Path $BuildProfile 'snowball-build-venv'
& $Python -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated build environment' }
$Python = Join-Path $Venv 'Scripts/python.exe'
& $Python -m pip install --disable-pip-version-check --only-binary=:all: -r (Join-Path $PSScriptRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pinned build dependency installation failed' }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Inconsistent build dependencies' }
$Tests = @(Get-Content -Raw (Join-Path $PSScriptRoot 'ci_tests.json') | ConvertFrom-Json)
foreach ($Test in $Tests) { if (-not (Test-Path $Test -PathType Leaf)) { throw "Required Windows test is absent: $Test" } }
$env:PYTHONPATH = (Join-Path $Repo 'codex/companion') + ';' + (Join-Path $Repo 'codex/tools')
& $Python -m pytest @Tests -q --junitxml=windows-tests.xml
if ($LASTEXITCODE -ne 0) { throw 'Windows test suite failed' }
& (Join-Path $PSScriptRoot 'build.ps1') -Python $Python
& $Python (Join-Path $PSScriptRoot 'smoke_distribution.py') (Join-Path $Repo 'dist/windows/Snowball')
if ($LASTEXITCODE -ne 0) { throw 'Packaged EXE smoke checks failed' }
$Archive = Join-Path $Repo 'dist/windows/Snowball-Windows-x64-0.6.0.zip'
& $Python (Join-Path $PSScriptRoot 'distribution_manifest.py') (Join-Path $Repo 'dist/windows/Snowball') --archive $Archive
if ($LASTEXITCODE -ne 0) { throw 'Native ZIP content verification failed' }
$Digest = (Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($Archive + '.sha256', $Digest + '  ' + [IO.Path]::GetFileName($Archive) + "`n", (New-Object Text.UTF8Encoding($false)))
