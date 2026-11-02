param(
    [ValidateSet('Install','Start','Status','Stop','Remove')][string]$Action = 'Status',
    [string]$Python
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This manager requires Windows.' }
$taskName = 'Snowball Companion Auto Reconnect'
$supervisor = Join-Path $PSScriptRoot 'windows_supervisor.py'
$ownerMarker = 'Snowball Companion Windows runtime: ' + $PSScriptRoot
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $ownerMarker) {
    throw 'An existing task belongs to another installation. Inspect its action before migrating it.'
}
if ($Action -eq 'Install') {
    if (-not $Python) { $Python = Join-Path (Split-Path $PSScriptRoot) '.venv/Scripts/pythonw.exe' }
    $Python = (Resolve-Path -LiteralPath $Python).Path
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $taskAction = New-ScheduledTaskAction -Execute $Python -Argument ('-X utf8 "' + $supervisor + '"') -WorkingDirectory $PSScriptRoot
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Description $ownerMarker -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
} elseif ($Action -eq 'Start') {
    if (-not $existing) { throw 'Run Install first.' }
    Start-ScheduledTask -TaskName $taskName
} elseif ($Action -in @('Stop','Remove')) {
    if ($existing) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
    # Stop only this user's processes with one of this runtime's exact script paths.
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $scripts = @('windows_supervisor.py','status_hub.py','bridge.py') | ForEach-Object { Join-Path $PSScriptRoot $_ }
    foreach ($process in (Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'")) {
        $matchesScript = $false
        foreach ($script in $scripts) {
            if ($process.CommandLine -match ('(?:^|[\s"])' + [regex]::Escape($script) + '(?:$|[\s"])')) { $matchesScript = $true }
        }
        if ($matchesScript) {
            $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction SilentlyContinue
            if ($owner.Sid -eq $sid) { Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue }
        }
    }
    if ($Action -eq 'Remove' -and $existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
}
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    $task | Select-Object TaskName,State,Description
    Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime,LastTaskResult,NextRunTime
} else { Write-Output 'No login task registered. Pairing and Pet data were preserved.' }
