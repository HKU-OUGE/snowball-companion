@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Open-Snowball.ps1"
if errorlevel 1 pause
