@echo off
cd /d "%~dp0"
chcp 65001 >nul
title LanYin

set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" 2>nul
if %ERRORLEVEL%==0 set "PY=py -3"
if "%PY%"=="" (
  python -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" 2>nul
  if %ERRORLEVEL%==0 set "PY=python"
)
if "%PY%"=="" (
  echo [ERROR] Python 3.11+ not found.
  echo Install it from https://www.python.org/downloads/
  echo and check "Add Python to PATH".
  pause
  exit /b 1
)

%PY% "%~dp0start_win.py"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Program exited with an error.
  pause
)
exit /b %ERR%
