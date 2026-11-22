/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "nb-text.h"

void nb_bitmap_free(struct nb_bitmap *b)
{
	if (!b)
		return;
	free(b->data);
	free(b->stroke);
	memset(b, 0, sizeof(*b));
}

static wchar_t *utf8_to_wide(const char *s, int *out_len)
{
	*out_len = 0;
	if (!s || !*s)
		return NULL;
	int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
	if (n <= 1)
		return NULL;
	wchar_t *w = malloc(sizeof(wchar_t) * (size_t)n);
	if (!w)
		return NULL;
	MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
	/* 去掉 \r，制表符换成 4 个空格以外的简单处理：换成空格 */
	int j = 0;
	for (int i = 0; i < n - 1; i++) {
		wchar_t c = w[i];
		if (c == L'\r')
			continue;
		if (c == L'\t')
			c = L' ';
		w[j++] = c;
	}
	w[j] = 0;
	*out_len = j;
	return w;
}

static bool is_cjk(wchar_t c)
{
	return (c >= 0x2E80 && c <= 0x9FFF) || (c >= 0xAC00 && c <= 0xD7AF) || (c >= 0xF900 && c <= 0xFAFF) ||
	       (c >= 0xFE30 && c <= 0xFE4F) || (c >= 0xFF00 && c <= 0xFFEF) || (c >= 0xD800 && c <= 0xDFFF);
}

/* 不能出现在行首的标点（简单避头） */
static bool no_line_start(wchar_t c)
{
	static const wchar_t *list = L"，。、；：？！）】》」』〉”’…—,.;:?!)]}%";
	return c && wcschr(list, c) != NULL;
}

struct line {
	int start, len, width;
};

struct layout {
	struct line *lines;
	int count, cap;
};

static void push_line(struct layout *l, int start, int len, int width)
{
	if (l->count == l->cap) {
		l->cap = l->cap ? l->cap * 2 : 32;
		l->lines = realloc(l->lines, sizeof(struct line) * (size_t)l->cap);
	}
	l->lines[l->count++] = (struct line){start, len, width};
}

static int text_width(HDC dc, const wchar_t *s, int len)
{
	SIZE sz = {0, 0};
	if (len > 0)
		GetTextExtentPoint32W(dc, s, len, &sz);
	return sz.cx;
}

static int font_pad(const struct nb_font *f)
{
	return f->outline > 0 ? f->outline + 2 + (f->italic ? f->size / 3 : 0) : 0;
}

/* Exact squared Euclidean distance transform (lower envelope of parabolas).
 * O(w*h), so a 20px outline never does radius-squared work per pixel. */
static void distance_1d(const float *f, float *d, int n, int *v, float *z)
{
	int k = 0; v[0] = 0; z[0] = -1e20f; z[1] = 1e20f;
	for (int q = 1; q < n; q++) {
		float at;
		for (;;) {
			int j = v[k];
			at = ((f[q] + (float)q*q) - (f[j] + (float)j*j)) / (2.0f*(q-j));
			if (at > z[k] || k == 0) break;
			k--;
		}
		k++; v[k] = q; z[k] = at; z[k+1] = 1e20f;
	}
	k = 0;
	for (int q = 0; q < n; q++) {
		while (z[k+1] < q) k++;
		float delta = (float)(q-v[k]); d[q] = delta*delta + f[v[k]];
	}
}

static void stroke_bitmap(struct nb_bitmap *b, int radius)
{
	if (!b->data || radius <= 0) return;
	int w = b->w, h = b->h, n = w > h ? w : h;
	size_t pixels = (size_t)w*h;
	float *tmp = malloc(pixels*sizeof(float));
	float *f = malloc(n*sizeof(float)), *d = malloc(n*sizeof(float));
	float *z = malloc((n+1)*sizeof(float)); int *v = malloc(n*sizeof(int));
	b->stroke = malloc(pixels);
	if (!tmp || !f || !d || !z || !v || !b->stroke) {
		free(b->stroke); b->stroke = NULL; goto done;
	}
	for (int y = 0; y < h; y++) {
		for (int x = 0; x < w; x++) f[x] = b->data[(size_t)y*w+x] > 32 ? 0 : 1e10f;
		distance_1d(f, d, w, v, z);
		memcpy(tmp+(size_t)y*w, d, w*sizeof(float));
	}
	for (int x = 0; x < w; x++) {
		for (int y = 0; y < h; y++) f[y] = tmp[(size_t)y*w+x];
		distance_1d(f, d, h, v, z);
		for (int y = 0; y < h; y++) {
			size_t at = (size_t)y*w+x;
			float cov = radius + 1.0f - sqrtf(d[y]);
			if (cov < 0) cov = 0;
			if (cov > 1) cov = 1;
			/* Draw just the exterior contour, never a translated shadow or
			 * opaque glyph interior; fill opacity remains independent. */
			b->stroke[at] = (uint8_t)lroundf(cov*(255-b->data[at]));
		}
	}
done:
	free(tmp); free(f); free(d); free(z); free(v);
}

/* Natural width of explicit lines, bounded before allocating a bitmap. */
int nb_measure_width(const char *utf8, const struct nb_font *font)
{
	int n = 0;
	wchar_t *w = utf8_to_wide(utf8, &n);
	if (!w) return 0;
	HDC dc = CreateCompatibleDC(NULL);
	HFONT hf = CreateFontW(-font->size, 0, 0, 0, font->bold ? FW_BOLD : FW_NORMAL,
		font->italic, font->underline, font->strike, DEFAULT_CHARSET, OUT_TT_PRECIS,
		CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
		font->face[0] ? font->face : L"Microsoft YaHei");
	HGDIOBJ old = SelectObject(dc, hf);
	int maxw = 0, start = 0;
	for (int i = 0; i <= n; i++) {
		if (i == n || w[i] == L'\n') {
			int len = i - start;
			/* GDI extent API accepts at most 8192 UTF-16 units reliably. */
			int tw = len > 8192 ? 4096 : text_width(dc, w + start, len);
			if (tw > maxw) maxw = tw;
			start = i + 1;
		}
	}
	SelectObject(dc, old); DeleteObject(hf); DeleteDC(dc); free(w);
	maxw += font_pad(font) * 2;
	return maxw > 4096 ? 4096 : maxw;
}

/* 按宽度折行：英文优先在空格处断，中文任意处断，超长单词强制断开（overflow-wrap:anywhere）。 */
static void wrap_paragraph(HDC dc, const wchar_t *w, int start, int end, int width, struct layout *out)
{
	if (start >= end) {
		push_line(out, start, 0, 0);
		return;
	}
	int pos = start;
	bool first = true;
	while (pos < end) {
		if (!first) /* 折行后的行首空格吞掉 */
			while (pos < end && w[pos] == L' ')
				pos++;
		if (pos >= end)
			break;
		first = false;
		int remain = end - pos;
		int fit = 0;
		SIZE sz;
		if (!GetTextExtentExPointW(dc, w + pos, remain, width, &fit, NULL, &sz))
			fit = remain;
		if (fit >= remain) {
			push_line(out, pos, remain, text_width(dc, w + pos, remain));
			break;
		}
		if (fit < 1)
			fit = 1;
		/* 不拆开代理对 */
		if (fit < remain && w[pos + fit] >= 0xDC00 && w[pos + fit] <= 0xDFFF && fit > 1)
			fit--;
		int brk = -1;
		for (int i = fit; i > 0; i--) {
			wchar_t prev = w[pos + i - 1], next = w[pos + i];
			bool ok = prev == L' ' || next == L' ' || is_cjk(prev) || is_cjk(next);
			if (prev >= 0xD800 && prev <= 0xDBFF)
				ok = false;
			if (ok && no_line_start(next) && i > 1)
				continue;
			if (ok) {
				brk = i;
				break;
			}
		}
		if (brk <= 0)
			brk = fit;
		int len = brk;
		/* 行尾空格不计宽度 */
		int vis = len;
		while (vis > 0 && w[pos + vis - 1] == L' ')
			vis--;
		push_line(out, pos, len, text_width(dc, w + pos, vis));
		pos += len;
	}
}

bool nb_raster_text(const char *utf8, const struct nb_font *font, int width, enum nb_align align,
		    double line_height, bool single_line, struct nb_bitmap *out)
{
	memset(out, 0, sizeof(*out));
	int wlen = 0;
	wchar_t *w = utf8_to_wide(utf8, &wlen);
	if (!w)
		return false;
	int pad = font_pad(font);
	if (width < 8 + pad*2) width = 8 + pad*2;
	int layout_width = width - pad*2;

	HDC dc = CreateCompatibleDC(NULL);
	int size = font->size < 6 ? 6 : font->size;
	HFONT hf = CreateFontW(-size, 0, 0, 0, font->bold ? FW_BOLD : FW_NORMAL, font->italic, font->underline,
			       font->strike, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY,
			       DEFAULT_PITCH | FF_DONTCARE, font->face[0] ? font->face : L"Microsoft YaHei");
	HGDIOBJ old_font = SelectObject(dc, hf);
	TEXTMETRICW tm;
	GetTextMetricsW(dc, &tm);

	struct layout lay = {0};
	wchar_t *ellipsized = NULL;
	if (single_line) {
		/* 标题：只取第一行 */
		int n = 0;
		while (n < wlen && w[n] != L'\n')
			n++;
		while (n > 0 && w[n - 1] == L' ')
			n--;
		int tw = text_width(dc, w, n);
		if (n > 0 && tw > layout_width) {
			int ew = text_width(dc, L"…", 1);
			int fit = 0;
			SIZE sz;
			GetTextExtentExPointW(dc, w, n, layout_width - ew > 1 ? layout_width - ew : 1, &fit, NULL, &sz);
			if (fit > 0 && w[fit] >= 0xDC00 && w[fit] <= 0xDFFF)
				fit--;
			ellipsized = malloc(sizeof(wchar_t) * (size_t)(fit + 2));
			memcpy(ellipsized, w, sizeof(wchar_t) * (size_t)fit);
			ellipsized[fit] = L'…';
			ellipsized[fit + 1] = 0;
			free(w);
			w = ellipsized;
			n = fit + 1;
			tw = text_width(dc, w, n);
		}
		if (n > 0)
			push_line(&lay, 0, n, tw);
	} else {
		int s = 0;
		for (int i = 0; i <= wlen; i++) {
			if (i == wlen || w[i] == L'\n') {
				wrap_paragraph(dc, w, s, i, layout_width, &lay);
				s = i + 1;
			}
		}
	}

	bool any = false;
	for (int i = 0; i < lay.count; i++)
		if (lay.lines[i].len > 0)
			any = true;

	if (!any) {
		SelectObject(dc, old_font);
		DeleteObject(hf);
		DeleteDC(dc);
		free(lay.lines);
		free(w);
		return false;
	}

	int lh = (int)lround(size * (line_height < 1.0 ? 1.0 : line_height));
	if (lh < tm.tmHeight)
		lh = tm.tmHeight;
	int lines = lay.count;
	int max_h = NB_MAX_TEXTURE_HEIGHT;
	/* At most 8 Mi pixels of alpha per announcement (32 MiB temporary DIB). */
	if (max_h > 8388608 / width) max_h = 8388608 / width;
	if ((long long)lines * lh + pad*2 > max_h) {
		lines = (max_h - pad*2) / lh;
		out->truncated = true;
	}
	int H = lines * lh + pad*2;
	int W = width;

	BITMAPINFO bi = {0};
	bi.bmiHeader.biSize = sizeof(bi.bmiHeader);
	bi.bmiHeader.biWidth = W;
	bi.bmiHeader.biHeight = -H;
	bi.bmiHeader.biPlanes = 1;
	bi.bmiHeader.biBitCount = 32;
	bi.bmiHeader.biCompression = BI_RGB;
	void *bits = NULL;
	HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, NULL, 0);
	bool ok = false;
	if (bmp && bits) {
		HGDIOBJ old_bmp = SelectObject(dc, bmp);
		memset(bits, 0, (size_t)W * (size_t)H * 4);
		SetBkMode(dc, TRANSPARENT);
		SetTextColor(dc, RGB(255, 255, 255));
		SetTextAlign(dc, TA_TOP | TA_LEFT | TA_NOUPDATECP);
		int yoff = (lh - tm.tmHeight) / 2;
		for (int i = 0; i < lines; i++) {
			struct line *ln = &lay.lines[i];
			if (ln->len <= 0)
				continue;
			int x = pad;
			if (align == NB_ALIGN_CENTER)
				x = (W - ln->width) / 2;
			else if (align == NB_ALIGN_RIGHT)
				x = W - pad - ln->width;
			int len = ln->len;
			while (len > 0 && w[ln->start + len - 1] == L' ' && align != NB_ALIGN_LEFT)
				len--;
			TextOutW(dc, x, pad + i * lh + yoff, w + ln->start, len);
		}
		GdiFlush();
		out->data = malloc((size_t)W * (size_t)H);
		if (out->data) {
			const uint8_t *src = bits;
			for (size_t p = 0, n = (size_t)W * (size_t)H; p < n; p++) {
				uint8_t b = src[p * 4], g = src[p * 4 + 1], r = src[p * 4 + 2];
				uint8_t m = r > g ? r : g;
				out->data[p] = m > b ? m : b;
			}
			out->w = W;
			out->h = H;
			out->lines = lines;
			for (int i = 0; i < lines; i++)
				if (lay.lines[i].width > out->text_w)
					out->text_w = lay.lines[i].width;
			stroke_bitmap(out, font->outline);
			ok = true;
		}
		SelectObject(dc, old_bmp);
	}
	if (bmp)
		DeleteObject(bmp);
	SelectObject(dc, old_font);
	DeleteObject(hf);
	DeleteDC(dc);
	free(lay.lines);
	free(w);
	return ok;
}
