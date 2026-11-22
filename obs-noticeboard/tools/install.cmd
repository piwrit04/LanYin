@echo off
chcp 65001 >nul
setlocal
rem 把公告板插件装到 %ProgramData%\obs-studio\plugins\obs-noticeboard （OBS 32 会自动读取这里）
set "SRC=%~dp0"
set "DEST=%ProgramData%\obs-studio\plugins\obs-noticeboard"
if not exist "%SRC%obs-plugins\64bit\obs-noticeboard.dll" (
  echo 找不到 obs-plugins\64bit\obs-noticeboard.dll，请先把压缩包完整解压再运行。
  pause
  exit /b 1
)
mkdir "%DEST%\bin\64bit" 2>nul
mkdir "%DEST%\data" 2>nul
copy /y "%SRC%obs-plugins\64bit\obs-noticeboard.dll" "%DEST%\bin\64bit\" >nul || goto fail
xcopy /e /i /y "%SRC%data\obs-plugins\obs-noticeboard\*" "%DEST%\data\" >nul || goto fail
echo.
echo 安装完成：%DEST%
echo 如果 OBS 正开着，请关掉 OBS 再重新打开。
echo 然后在「来源」下面点 + ，选「公告板」。
echo.
pause
exit /b 0
:fail
echo 复制失败。请关掉 OBS 后再试，或者右键「以管理员身份运行」。
pause
exit /b 1
