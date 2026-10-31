@echo off
rem Removes the ONVIF VCam Windows service and the tray autostart (files and settings are kept).
call "%~dp0_env.cmd"
net session >nul 2>&1 || (echo Right-click SERVICE-UNINSTALL.bat and choose "Run as administrator". & pause & exit /b 1)
"%PY%" -m vcam.winservice remove vcam
pause
