@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 bind_to_obs.py
) else (
  python bind_to_obs.py
)
echo.
pause
