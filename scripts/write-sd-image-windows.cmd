@echo off
setlocal
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0sd-image-writer-windows.ps1" %*
if errorlevel 1 pause
