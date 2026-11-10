@echo off
cd /d "%~dp0"
chcp 65001 >nul
title LanYin

rem Pick Python 3.11+. "if errorlevel" is checked at run time; %ERRORLEVEL%
rem inside a ( ) block is expanded too early, so the "python" fallback never ran.
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
%PY% "%~dp0start_win.py"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Program exited with an error.
  pause
)
exit /b %ERR%
