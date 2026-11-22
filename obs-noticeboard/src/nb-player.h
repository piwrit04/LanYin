/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
/* 公告板播放时间线（纯逻辑，无 OBS / Windows 依赖，可单元测试）。
 * 与网页版 board.js / server.py 的 cycleDuration / frameAt / _tick 保持一致。 */
#pragma once
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

struct nb_play {
	double interval;    /* 每条至少停留秒数 */
	double speed;       /* 滚动速度 px/s */
	double top_dwell;   /* 顶部停留秒数 */
	double bottom_dwell;/* 底部停留秒数 */
	double fade;        /* 淡入 / 淡出秒数 */
	bool rotate;        /* true = 自动轮播 */
};

struct nb_frame {
	double offset;  /* 正文向上滚动的像素 */
	double opacity; /* 内容不透明度 0..1 */
};

/* 一条公告完整播放一轮的时长（含淡入淡出）。 */
double nb_cycle_duration(double distance, const struct nb_play *p);

/* 某一时刻的滚动位置与不透明度。cycles = 是否循环（固定模式下长文循环滚动）。 */
struct nb_frame nb_frame_at(double elapsed, double distance, const struct nb_play *p, bool cycles);

/* 当前条目的画面：根据是否轮播、条目数自动选择循环方式。 */
struct nb_frame nb_frame_for(double elapsed, double distance, const struct nb_play *p, int count);

/* 推进时间线。返回 true 表示应切换到下一条（调用方负责换条并把 elapsed 归零）。 */
bool nb_advance(double *elapsed, double dt, double distance, const struct nb_play *p, int count, bool paused);

#ifdef __cplusplus
}
#endif
