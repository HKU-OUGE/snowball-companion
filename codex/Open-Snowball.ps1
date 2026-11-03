param([string]$Python, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
try {
    if (-not $Python) {
        $localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
        if (Test-Path -LiteralPath $localPython) { $Python = $localPython }
        else { $Python = (Get-Command python -ErrorAction Stop).Source }
    }
    $arguments = @('-X','utf8',(Join-Path $PSScriptRoot 'companion/console_launcher.py'),'open')
    if ($NoBrowser) { $arguments += '--no-browser' }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'The local console did not start. Keep the complete plugin package in a fixed folder and check Python.' }
} catch {
    Write-Error $_ -ErrorAction Continue
    if (-not $NoBrowser) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show('Snowball console could not start. Check that Python is installed and the complete package has been extracted. Run Open-Snowball.ps1 in PowerShell to view the error.', 'Snowball Companion') | Out-Null
    }
    exit 1
}
