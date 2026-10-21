@echo off
setlocal
call "%~dp0_env.cmd"
rem Run while START.bat is running. Usage: TEST.bat [ip] [http_port] [rtsp_port] [user] [password]
set "IP=%~1"
if "%IP%"=="" set "IP=127.0.0.1"
set "HP=%~2"
if "%HP%"=="" set "HP=8080"
set "RP=%~3"
if "%RP%"=="" set "RP=8554"
set "U=%~4"
if "%U%"=="" set "U=admin"
set "P=%~5"
if "%P%"=="" set "P=admin"
echo ===== Protocol checks (auth, faults, Media2/H.265, discovery, events, RTSP) =====
"%PY%" app\tests\test_raw_soap.py %IP% %HP% %RP% %U% %P%
echo.
echo ===== Third-party ONVIF client (official WSDL/XSD) =====
"%PY%" app\tests\test_with_onvif_client.py %IP% %HP% %U% %P%
echo ===== Control panel API (port 8000) =====
"%PY%" app\tests\test_panel.py %IP% 8000 %U% %P%
echo.
pause
