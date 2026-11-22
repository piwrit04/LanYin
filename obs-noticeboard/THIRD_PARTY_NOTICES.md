# 许可证与依赖

本目录的公告板代码与文档：Copyright (C) 2026 星澪，GPL-2.0-or-later，完整许可见 [LICENSE](LICENSE)。此许可仅适用于本独立子项目，不替澜音的其他目录另行声明许可。

- OBS Studio / libobs：https://github.com/obsproject/obs-studio ，OBS Project contributors，GPL-2.0-or-later。插件通过公开 libobs API 链接；滚动语义和属性刷新行为参考 OBS 32.2.2。未随仓库提供 OBS 的官方 DLL、库、头文件或字体，构建时需自行取得匹配版本。
- `cmake/obsconfig.h` 只是本项目的三行构建配置 shim，不是复制的 OBS 官方头文件。
- Windows GDI / 系统字体：由使用者自己的 Windows 安装提供。本仓库不重新分发字体；预览仅为通用示例文字的实际渲染。
- CMake、Ninja、MinGW-w64、Python 和 C 编译器仅为外部构建工具，不在本目录中重新分发。
