@echo off
rem Start the virtual cameras automatically (in the background) when this Windows user logs in.
set "SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%SU%\ONVIF-VCam.cmd" echo @call "%~dp0START.bat" silent
echo Autostart enabled: "%SU%\ONVIF-VCam.cmd"
pause
