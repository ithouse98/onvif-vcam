@echo off
setlocal
call "%~dp0_env.cmd"
sc query ONVIF-VCam 2>nul | find "RUNNING" >nul && ("%PY%" -m vcam.winservice stop vcam >nul 2>&1 & echo ONVIF VCam service stopped. & timeout /t 3 >nul & exit /b 0)
"%PY%" -m vcam -c "%ROOT%config.json" --stop >nul 2>&1
if errorlevel 1 (echo ONVIF VCam is not running.) else (echo ONVIF VCam stopped.)
timeout /t 3 >nul
