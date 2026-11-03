param([switch]$Plan)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$taskName = 'Snowball Companion Auto Reconnect'
if ($Plan) { @{ task=$taskName; userOnly=$true; mutation=$false } | ConvertTo-Json -Compress; exit 0 }
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) { @{state='absent'} | ConvertTo-Json -Compress; exit 0 }
$prefix = 'Snowball Companion Windows runtime: '
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$user = $task.Principal.UserId
$sameUser = $user -eq $identity.Name -or $user -eq $identity.User.Value
if (-not $sameUser) {
    try { $sameUser = ([System.Security.Principal.NTAccount]::new($user)).Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $identity.User.Value } catch { $sameUser=$false }
}
$valid = $sameUser -and $task.Description -and $task.Description.StartsWith($prefix) -and $task.Actions.Count -eq 1
if ($valid) {
    $runtime = $task.Description.Substring($prefix.Length)
    $script = Join-Path $runtime 'windows_supervisor.py'
    $valid = (Test-Path -LiteralPath $script -PathType Leaf) -and ($task.Actions[0].Arguments -match ('(?:^|[\s"])'+[regex]::Escape($script)+'(?:$|[\s"])'))
    $valid = $valid -and ($task.Principal.LogonType -eq 'Interactive') -and ($task.Principal.RunLevel -eq 'Limited')
}
$state = if (-not $valid) {'conflict'} elseif ($task.State -eq 'Disabled') {'inactive'} else {'ready'}
@{state=$state} | ConvertTo-Json -Compress
