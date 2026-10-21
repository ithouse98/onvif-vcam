@echo off
setlocal
call "%~dp0_env.cmd"
"%PY%" -m vcam -c "%ROOT%config.json" --stop >nul 2>&1
if errorlevel 1 (echo ONVIF VCam is not running.) else (echo ONVIF VCam stopped.)
timeout /t 3 >nul
