/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
/* 用 Windows GDI 把纯文本排版并栅格化成单通道（覆盖率）位图。 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum nb_align { NB_ALIGN_LEFT = 0, NB_ALIGN_CENTER = 1, NB_ALIGN_RIGHT = 2 };

struct nb_font {
	wchar_t face[64];
	int size; /* 字号（像素，等同网页 font-size） */
	bool bold, italic, underline, strike;
	int outline; /* outward stroke radius; 0 preserves 1.3 metrics */
};

struct nb_bitmap {
	int w, h;
	uint8_t *stroke; /* expanded glyph contour, independent color/opacity */
	uint8_t *data; /* w*h 字节，0..255 覆盖率 */
	int lines;
	int text_w; /* 最宽一行的实际像素宽度 */
	bool truncated; /* 文字过长，超出显卡纹理上限被截断 */
};

#define NB_MAX_TEXTURE_HEIGHT 16384

/* single_line = 只排一行，超宽用「…」省略（标题）。line_height = 行高倍数。
 * 返回 false 表示没有可显示的文字（out 为空）。 */
bool nb_raster_text(const char *utf8, const struct nb_font *font, int width, enum nb_align align,
		    double line_height, bool single_line, struct nb_bitmap *out);
int nb_measure_width(const char *utf8, const struct nb_font *font);
void nb_bitmap_free(struct nb_bitmap *b);

#ifdef __cplusplus
}
#endif
