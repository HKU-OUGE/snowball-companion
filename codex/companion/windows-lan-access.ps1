param(
    [Parameter(Mandatory=$true)][string]$Python,
    [switch]$Plan,
    [switch]$Remove,
    [switch]$Elevated
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
if ($env:OS -ne 'Windows_NT') { throw 'This helper requires Windows.' }
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
if ([IO.Path]::GetFileName($pythonPath) -notin @('python.exe','pythonw.exe')) { throw 'Expected the configured Python interpreter.' }
$hash = [Security.Cryptography.SHA256]::Create()
try { $suffix = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot)))).Replace('-','').Substring(0,12) }
finally { $hash.Dispose() }
$ruleName = 'SnowballCompanionLAN-' + $suffix
$description = 'Snowball Companion owned LAN gateway: ' + $PSScriptRoot
$parameters = [ordered]@{Name=$ruleName;DisplayName='Snowball Companion LAN';Description=$description;Group='Snowball Companion';Direction='Inbound';Action='Allow';Protocol='TCP';LocalPort=4193;RemoteAddress='LocalSubnet';Profile='Private';Program=$pythonPath}
if ($Plan) { $parameters | ConvertTo-Json -Compress; exit 0 }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Administrator approval was not granted.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Python "' + $pythonPath + '" -Elevated'
    if ($Remove) { $arguments += ' -Remove' }
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe') -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) { throw 'This rule belongs to another installation.' }
if ($Remove) {
    if ($existing) { Remove-NetFirewallRule -Name $ruleName }
    exit 0
}
if ($existing) {
    $filter = $existing | Get-NetFirewallApplicationFilter
    if ($filter.Program -ne $pythonPath) { throw 'The existing gateway rule uses a different interpreter; remove the owned old rule explicitly first.' }
    Set-NetFirewallRule -Name $ruleName -Enabled True -Profile Private -RemoteAddress LocalSubnet -Action Allow -Protocol TCP -LocalPort 4193 -Program $pythonPath | Out-Null
} else {
    New-NetFirewallRule @parameters | Out-Null
}
$actual = Get-NetFirewallRule -Name $ruleName
$port = $actual | Get-NetFirewallPortFilter
$address = $actual | Get-NetFirewallAddressFilter
$application = $actual | Get-NetFirewallApplicationFilter
if ($actual.Enabled -ne 'True' -or $actual.Profile -ne 'Private' -or $actual.Action -ne 'Allow' -or $port.LocalPort -ne '4193' -or $port.Protocol -ne 'TCP' -or $address.RemoteAddress -ne 'LocalSubnet' -or $application.Program -ne $pythonPath) {
    throw 'The private LAN rule did not verify.'
}
Write-Output 'Private-network access to this Snowball gateway on TCP 4193 is enabled.'
