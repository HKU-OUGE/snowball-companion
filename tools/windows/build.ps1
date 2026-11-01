param([string]$Python = 'python', [string]$Output = '')
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) { throw 'Native Windows x64 is required' }
if (-not $Output) { $Output = Join-Path $Repo 'dist/windows' }
$Work = Join-Path $Repo ('build/windows-' + [Guid]::NewGuid().ToString('N'))
$Payload = Join-Path $Work 'payload'
& $Python (Join-Path $PSScriptRoot 'stage_payload.py') --root $Repo --output $Payload
if ($LASTEXITCODE -ne 0) { throw 'Reviewed payload staging failed' }
$env:SNOWBALL_STAGED_PAYLOAD = $Payload
& $Python -m PyInstaller --noconfirm --clean --distpath $Output --workpath (Join-Path $Work 'pyinstaller') (Join-Path $PSScriptRoot 'Snowball.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
& $Python (Join-Path $PSScriptRoot 'stage_payload.py') --verify --output (Join-Path $Output 'Snowball/_internal/bundled')
if ($LASTEXITCODE -ne 0) { throw 'Frozen payload verification failed' }
& $Python (Join-Path $PSScriptRoot 'distribution_manifest.py') (Join-Path $Output 'Snowball')
if ($LASTEXITCODE -ne 0) { throw 'Native installation manifest generation failed' }
Write-Output (Join-Path $Output 'Snowball')
