@echo off
rem Run on ANOTHER PC: installs the screen agent as a Windows service. The agent connects to the
rem ONVIF VCam server and appears in its control panel (Agents), where its monitors become cameras.
call "%~dp0_env.cmd"
net session >nul 2>&1 || (echo Right-click AGENT-INSTALL.bat and choose "Run as administrator". & pause & exit /b 1)
echo.
echo ONVIF VCam screen agent
echo -----------------------
set "SERVER="
set /p "SERVER=VCam server IP (Enter = find automatically on the network): "
set "KEY="
set /p "KEY=Agent key (Enter = none; must match the key in the VCam panel): "
set "ARGS="
if not "%SERVER%"=="" set ARGS=%ARGS% --server %SERVER%
if not "%KEY%"=="" set ARGS=%ARGS% --key %KEY%
"%PY%" -m vcam.winservice install agent %ARGS%
if errorlevel 1 (pause & exit /b 1)
netsh advfirewall firewall delete rule name="ONVIF-VCam-Agent" >nul 2>&1
netsh advfirewall firewall add rule name="ONVIF-VCam-Agent" dir=out action=allow program="%ROOT%runtime\python.exe" enable=yes profile=any >nul
echo.
echo Done. Service "ONVIF-VCam-Agent" is running and starts with Windows.
echo In the VCam control panel open the "Agents" tab: this PC is listed there.
echo Note: the screen is captured while a user is logged in to this PC.
pause
