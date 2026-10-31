@echo off
rem Installs ONVIF VCam as a Windows service (starts with Windows, even before anyone logs in)
rem and the tray icon for every user. Screen cameras capture the logged-in user's desktop.
call "%~dp0_env.cmd"
net session >nul 2>&1 || (echo Right-click SERVICE-INSTALL.bat and choose "Run as administrator". & pause & exit /b 1)
"%PY%" -m vcam.winservice stop vcam >nul 2>&1
"%PY%" -m vcam -c "%ROOT%config.json" --stop >nul 2>&1
"%PY%" -m vcam.winservice install vcam
if errorlevel 1 (pause & exit /b 1)
start "" "%PYW%" "%ROOT%app\vcam\tray.py"
echo.
echo Done. Service "ONVIF-VCam" is running; the tray icon is next to the clock.
echo Control panel: http://127.0.0.1:8000
pause
