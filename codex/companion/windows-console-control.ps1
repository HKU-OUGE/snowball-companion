param([string]$Runtime,[switch]$Stop,[switch]$Plan)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
if (-not [System.IO.Path]::IsPathRooted($Runtime)) { throw 'Runtime must be absolute.' }
$script=Join-Path $Runtime 'console_server.py'
if ($Plan) { @{script=$script;currentUserOnly=$true;exactlyOne=$true;stopRequested=[bool]$Stop}|ConvertTo-Json -Compress;exit 0 }
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'Console script missing.' }
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$matches=@(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
    if ($_.CommandLine -notmatch ('(?:^|[\s"])'+[regex]::Escape($script)+'(?:$|[\s"])')) { return $false }
    $owner=Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction SilentlyContinue
    return $owner.Sid -eq $sid
})
if ($matches.Count -ne 1) { throw 'Console owner is ambiguous.' }
if ($Stop) {
    $again=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$matches[0].ProcessId)
    if (-not $again -or $again.CreationDate -ne $matches[0].CreationDate -or $again.CommandLine -ne $matches[0].CommandLine) { throw 'Console process changed.' }
    Stop-Process -Id $again.ProcessId -ErrorAction Stop
}
@{verified=$true;stopped=[bool]$Stop}|ConvertTo-Json -Compress
