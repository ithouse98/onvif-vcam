@echo off
rem Runs the screen agent in this window (no service) - for a quick test. Ctrl+C to stop.
rem Usage: AGENT-RUN.bat [--server IP] [--key KEY]
call "%~dp0_env.cmd"
"%PY%" -m vcam.agent --config "%ROOT%agent.json" %*
pause
