param([Parameter(Mandatory=$true)][string]$Python)
# Only used by the disposable hosted runner, never by the product installer.
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    & (Join-Path $PSScriptRoot 'ci.ps1') -Python $Python
    exit 0
}
$Name = 'sbci' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$Bytes = New-Object byte[] 36
$Rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$Rng.GetBytes($Bytes); $Rng.Dispose()
$Password = [Convert]::ToBase64String($Bytes) + 'aA1!'
Write-Output "::add-mask::$Password"
$Secure = ConvertTo-SecureString $Password -AsPlainText -Force
$Credential = New-Object Management.Automation.PSCredential("$env:COMPUTERNAME\$Name", $Secure)
$User = $null
try {
    $User = New-LocalUser -Name $Name -Password $Secure -Description 'Disposable Snowball build validation' -AccountNeverExpires
    $Users = Get-LocalGroup -SID 'S-1-5-32-545'
    if (-not (Get-LocalGroupMember -Group $Users | Where-Object { $_.SID.Value -eq $User.SID.Value })) {
        Add-LocalGroupMember -Group $Users -Member $User
    }
    # The standard token may read source/Python and write build outputs. This
    # does not relax token/credential ACLs created within its own profile.
    & icacls.exe $Repo /grant "*$($User.SID.Value):(OI)(CI)M" /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot grant checkout access to isolated build user' }
    $PythonRoot = Split-Path -Parent $Python
    & icacls.exe $PythonRoot /grant "*$($User.SID.Value):(OI)(CI)RX" /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot grant Python runtime access' }
    $LogDir = Join-Path $Repo 'windows-ci-logs'
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'ci.ps1') + '" -Python "' + $Python + '"'
    $Process = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Credential $Credential -LoadUserProfile -WorkingDirectory $Repo -ArgumentList $Arguments -PassThru -Wait -RedirectStandardOutput (Join-Path $LogDir 'stdout.txt') -RedirectStandardError (Join-Path $LogDir 'stderr.txt')
    Get-Content (Join-Path $LogDir 'stdout.txt')
    Get-Content (Join-Path $LogDir 'stderr.txt')
    if ($Process.ExitCode -ne 0) { throw "Standard-user Windows validation failed (exit $($Process.ExitCode)); see CI logs" }
} finally {
    if ($User) { Remove-LocalUser -Name $Name }
    $Password = $null; $Secure = $null; $Credential = $null
}
