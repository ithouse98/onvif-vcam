@echo off
setlocal
call "%~dp0_env.cmd"
rem Runs in the background (no console window). Log: vcam.log  - Stop: STOP.bat or the panel's power button.
rem No arguments: cameras from config.json (first run: one per video in "videos").
rem "START.bat silent": same, without opening the browser (used by autostart).
rem Other arguments go straight to vcam; use START-DEBUG.bat to see console output.
set "OPEN=--open"
if /i "%~1"=="silent" (set "OPEN=") else if not "%~1"=="" goto custom
if exist "%ROOT%config.json" (
    start "" "%PYW%" -m vcam -c "%ROOT%config.json" --log-file "%ROOT%vcam.log" %OPEN%
) else (
    start "" "%PYW%" -m vcam "%ROOT%videos" --mjpeg -c "%ROOT%config.json" --log-file "%ROOT%vcam.log" %OPEN%
)
exit /b 0
:custom
start "" "%PYW%" -m vcam %* --log-file "%ROOT%vcam.log"
