@echo off
chcp 65001 >nul
set "DEST=%ProgramData%\obs-studio\plugins\obs-noticeboard"
if exist "%DEST%" (
  rmdir /s /q "%DEST%"
  echo 已卸载公告板插件。场景里已有的公告板来源会显示为缺失，重新安装后会恢复。
) else (
  echo 没有找到已安装的公告板插件。
)
pause
