/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
#include "nb-player.h"
#include <math.h>

static double maxd(double a, double b) { return a > b ? a : b; }
static double mind(double a, double b) { return a < b ? a : b; }

double nb_cycle_duration(double distance, const struct nb_play *p)
{
	double speed = p->speed > 0.1 ? p->speed : 0.1;
	double content = distance > 0 ? maxd(p->interval, p->top_dwell + distance / speed + p->bottom_dwell)
				      : p->interval;
	return content + p->fade * 2.0;
}

struct nb_frame nb_frame_at(double elapsed, double distance, const struct nb_play *p, bool cycles)
{
	struct nb_frame f;
	double speed = p->speed > 0.1 ? p->speed : 0.1;
	double duration = nb_cycle_duration(distance, p);
	double t = maxd(0.0, elapsed);
	if (cycles && duration > 0)
		t = fmod(t, duration);
	f.offset = mind(distance, maxd(0.0, t - p->fade - p->top_dwell) * speed);
	if (f.offset < 0)
		f.offset = 0;
	f.opacity = 1.0;
	if (p->fade > 0) {
		f.opacity = mind(1.0, t / p->fade);
		if (cycles)
			f.opacity = mind(f.opacity, maxd(0.0, (duration - t) / p->fade));
	}
	return f;
}

struct nb_frame nb_frame_for(double elapsed, double distance, const struct nb_play *p, int count)
{
	bool rotate = p->rotate && count > 1;
	if (!rotate)
		return nb_frame_at(elapsed, distance, p, distance > 0);
	double d = nb_cycle_duration(distance, p);
	struct nb_frame f = nb_frame_at(mind(elapsed, d - 0.001), distance, p, false);
	if (p->fade > 0)
		f.opacity = mind(f.opacity, maxd(0.0, (d - elapsed) / p->fade));
	return f;
}

bool nb_advance(double *elapsed, double dt, double distance, const struct nb_play *p, int count, bool paused)
{
	if (paused)
		return false;
	if (dt < 0)
		dt = 0;
	if (dt > 1.0) /* 卡顿或长时间未渲染时不要一次跳过整条 */
		dt = 1.0;
	*elapsed += dt;
	bool rotate = p->rotate && count > 1;
	if (rotate)
		return *elapsed >= nb_cycle_duration(distance, p);
	/* 固定模式：循环滚动的长文按周期取模，避免浮点无限增大 */
	double d = nb_cycle_duration(distance, p);
	if (distance > 0 && d > 0 && *elapsed > d * 1000.0)
		*elapsed = fmod(*elapsed, d);
	else if (distance <= 0 && *elapsed > 1e6)
		*elapsed = 1e6;
	return false;
}
