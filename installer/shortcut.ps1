param([Parameter(Mandatory=$true)][string]$Python,[Parameter(Mandatory=$true)][string]$Launcher)
$ErrorActionPreference='Stop'
$shell=New-Object -ComObject WScript.Shell
$folder=Join-Path ([Environment]::GetFolderPath('Programs')) 'Snowball Companion'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$path=Join-Path $folder 'Snowball Console.lnk'
if(Test-Path -LiteralPath $path){
  $old=$shell.CreateShortcut($path)
  if($old.Description -ne 'Snowball Companion owned console'){throw 'An unrelated shortcut already exists.'}
}
$link=$shell.CreateShortcut($path)
$pythonw=Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
$link.TargetPath=if(Test-Path -LiteralPath $pythonw){$pythonw}else{$Python}
$link.Arguments='-X utf8 "'+$Launcher+'" --installer'
$link.WorkingDirectory=Split-Path -Parent $Launcher
$link.Description='Snowball Companion owned console'
$link.Save()
