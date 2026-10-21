@echo off
setlocal
call "%~dp0_env.cmd"
title ONVIF Virtual Camera (debug console)
rem Same as START.bat but in this console window with detailed logs (-v). Ctrl+C to stop.
if not "%~1"=="" goto custom
if exist "%ROOT%config.json" (
    "%PY%" -m vcam -c "%ROOT%config.json" --log-file "%ROOT%vcam.log" --open -v
) else (
    "%PY%" -m vcam "%ROOT%videos" --mjpeg -c "%ROOT%config.json" --log-file "%ROOT%vcam.log" --open -v
)
goto end
:custom
"%PY%" -m vcam %*
:end
echo.
pause
