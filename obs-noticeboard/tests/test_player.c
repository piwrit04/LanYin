/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
#include "../src/nb-player.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#define NEAR(a, b) (fabs((a) - (b)) < 1e-6)
int main(void)
{
	struct nb_play p = {12, 24, 2, 3, 0.45, false};
	/* 短文：周期 = interval + 2*fade */
	assert(NEAR(nb_cycle_duration(0, &p), 12.9));
	/* 长文：240px / 24 = 10s + 2 + 3 = 15 > 12 */
	assert(NEAR(nb_cycle_duration(240, &p), 15.9));
	/* 淡入 */
	struct nb_frame f = nb_frame_at(0.225, 0, &p, false);
	assert(NEAR(f.opacity, 0.5) && NEAR(f.offset, 0));
	/* 短文固定：不循环、不滚动、长期保持 1 */
	f = nb_frame_for(100, 0, &p, 1);
	assert(NEAR(f.opacity, 1) && NEAR(f.offset, 0));
	/* 长文：顶部停留期间不滚动 */
	f = nb_frame_for(0.45 + 1.9, 240, &p, 1);
	assert(NEAR(f.offset, 0));
	/* 滚动 5 秒 = 120px */
	f = nb_frame_for(0.45 + 2 + 5, 240, &p, 1);
	assert(NEAR(f.offset, 120));
	/* 到底后停在底部 */
	f = nb_frame_for(0.45 + 2 + 11, 240, &p, 1);
	assert(NEAR(f.offset, 240) && NEAR(f.opacity, 1));
	/* 固定模式长文循环：周期后回到顶部淡入 */
	f = nb_frame_for(15.9 + 0.1, 240, &p, 1);
	assert(NEAR(f.offset, 0) && f.opacity < 0.3);
	/* 轮播：滚动 + 停留完成前不切换 */
	p.rotate = true;
	double t = 0;
	int switched = 0;
	for (int i = 0; i < 15.8 * 60; i++)
		switched |= nb_advance(&t, 1.0 / 60, 240, &p, 3, false);
	assert(!switched);
	for (int i = 0; i < 12; i++)
		switched |= nb_advance(&t, 1.0 / 60, 240, &p, 3, false);
	assert(switched);
	/* 轮播末尾淡出 */
	f = nb_frame_for(15.9 - 0.225, 240, &p, 3);
	assert(NEAR(f.opacity, 0.5) && NEAR(f.offset, 240));
	/* 只有一条时即使是轮播也不切换 */
	t = 0;
	switched = 0;
	for (int i = 0; i < 60 * 60; i++)
		switched |= nb_advance(&t, 1.0 / 60, 0, &p, 1, false);
	assert(!switched);
	/* 暂停冻结 */
	t = 3;
	nb_advance(&t, 1, 0, &p, 3, true);
	assert(NEAR(t, 3));
	puts("player tests: OK");
	return 0;
}
