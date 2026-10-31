@echo off
rem Starts the ONVIF VCam tray icon (automatic at logon once SERVICE-INSTALL.bat was run).
call "%~dp0_env.cmd"
start "" "%PYW%" "%ROOT%app\vcam\tray.py"
