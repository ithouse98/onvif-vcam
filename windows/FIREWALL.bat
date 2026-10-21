@echo off
rem Allows the bundled python.exe and pythonw.exe (background mode) through Windows Firewall:
rem control panel, ONVIF HTTP, RTSP, RTP/UDP and WS-Discovery.
net session >nul 2>&1 || (echo Right-click FIREWALL.bat and choose "Run as administrator". & pause & exit /b 1)
netsh advfirewall firewall delete rule name="ONVIF-VCam" >nul 2>&1
for %%E in (python.exe pythonw.exe) do (
    netsh advfirewall firewall add rule name="ONVIF-VCam" dir=in action=allow program="%~dp0runtime\%%E" enable=yes profile=any >nul
    netsh advfirewall firewall add rule name="ONVIF-VCam" dir=out action=allow program="%~dp0runtime\%%E" enable=yes profile=any >nul
)
echo Done: firewall rules added for python.exe and pythonw.exe
pause
