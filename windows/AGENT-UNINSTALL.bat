@echo off
rem Removes the screen agent service from this PC.
call "%~dp0_env.cmd"
net session >nul 2>&1 || (echo Right-click AGENT-UNINSTALL.bat and choose "Run as administrator". & pause & exit /b 1)
"%PY%" -m vcam.winservice remove agent
pause
