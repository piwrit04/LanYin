@echo off
cd /d "%~dp0"
chcp 65001 >nul
title LanYin - bind to OBS

set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :run
python -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :run

echo [ERROR] Python 3.11+ not found.
echo Install it from https://www.python.org/downloads/
echo and check "Add Python to PATH".
pause
exit /b 1

:run
%PY% "%~dp0bind_to_obs.py"
echo.
pause
