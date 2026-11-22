cmake_minimum_required(VERSION 3.16)
project(obs-noticeboard VERSION 1.4.2 LANGUAGES C)

# OBS 源码（只用 libobs 头文件），请检出与 OBS 相同的版本，例如 32.2.2
set(OBS_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../obs-src" CACHE PATH "obs-studio 源码目录（需要 libobs/）")
# 链接用的 obs 库：
#   MinGW：直接指向 OBS 安装目录里的 obs.dll（GNU ld 可以直接链接 DLL）
#   MSVC ：指向由 obs.dll 生成的 obs.lib（见 README「自己编译」）
set(OBS_LINK_LIBRARY "" CACHE FILEPATH "obs.dll（MinGW）或 obs.lib（MSVC）")

if(NOT WIN32)
  message(FATAL_ERROR "公告板插件只支持 Windows（文字排版使用 GDI）。在 Linux 上请用 cmake/mingw-w64.cmake 交叉编译。")
endif()
if(NOT EXISTS "${OBS_SOURCE_DIR}/libobs/obs-module.h")
  message(FATAL_ERROR "找不到 ${OBS_SOURCE_DIR}/libobs/obs-module.h，请设置 -DOBS_SOURCE_DIR=")
endif()
if(NOT OBS_LINK_LIBRARY)
  message(FATAL_ERROR "请设置 -DOBS_LINK_LIBRARY=（obs.dll 或 obs.lib）")
endif()

add_library(obs-noticeboard MODULE
  src/plugin-main.c
  src/noticeboard.c
  src/nb-text.c
  src/nb-player.c)
set_target_properties(obs-noticeboard PROPERTIES PREFIX "" C_STANDARD 11 C_EXTENSIONS ON)
target_include_directories(obs-noticeboard PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/cmake"          # obsconfig.h
  "${OBS_SOURCE_DIR}/libobs")
target_link_libraries(obs-noticeboard PRIVATE "${OBS_LINK_LIBRARY}" gdi32 user32)
if(MSVC)
  target_compile_options(obs-noticeboard PRIVATE /utf-8 /W3)
  target_compile_definitions(obs-noticeboard PRIVATE _CRT_SECURE_NO_WARNINGS)
else()
  target_compile_options(obs-noticeboard PRIVATE -Wall -Wextra -Wno-unused-parameter -O2)
  target_link_options(obs-noticeboard PRIVATE -static-libgcc -Wl,--no-undefined -s)
endif()

# 安装成 OBS 标准目录结构：obs-plugins/64bit/*.dll + data/obs-plugins/obs-noticeboard/
install(TARGETS obs-noticeboard LIBRARY DESTINATION obs-plugins/64bit RUNTIME DESTINATION obs-plugins/64bit)
install(DIRECTORY data/ DESTINATION data/obs-plugins/obs-noticeboard)
