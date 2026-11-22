/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
/*
 * 公告板（obs-noticeboard）原生来源
 *
 * - 来源设置（属性窗口里编辑的内容）= 草稿
 * - 私有设置 private_settings["live"] = 已应用到直播的快照（随场景集合保存，属性窗口「取消」不会影响它）
 * - 画面：直播 / 录像 / 转场 / 滤镜都渲染到纹理 → 只显示「已应用」内容；
 *   直接画到窗口（属性窗口预览、工作室模式预览等）且草稿有改动时 → 显示草稿并标注「草稿预览」。
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <obs-module.h>
#include <util/dstr.h>
#include <util/platform.h>
#include <math.h>
#include <graphics/vec2.h>
#include <string.h>
#include <stdio.h>

#include "nb-player.h"
#include "nb-text.h"

#define NB_MAX_ITEMS 20
#define T(x) obs_module_text(x)

/* ---------------------------------------------------------------- 配置 */

struct nb_cfg_item {
	int slot; /* 1..NB_MAX_ITEMS，对应属性里的「公告 N」 */
	char *title;
	char *body;
};

struct nb_cfg {
	int width, height;
	struct nb_font title_font, body_font;
	uint32_t title_color, text_color, bg_color, border_color; /* 0xAABBGGRR（OBS 颜色格式） */
	double bg_opacity, border_opacity;                        /* 0..1 */
	int border_width, radius, padding, title_gap;
	double line_height;
	enum nb_align align;
	bool scrolling, scroll_axis, scroll_limit_width;
	double speed_x, speed_y, text_opacity, outline_opacity;
	bool outline;
	int outline_size, scroll_width;
	uint32_t outline_color;
	int max_height;
	struct nb_play play;
	int n;
	struct nb_cfg_item items[NB_MAX_ITEMS];
	struct dstr sig; /* 规范化后的全部配置，用来判断草稿和直播是否一致 */
};

struct nb_item {
	int slot;
	int width, height, content_w, content_h;
	struct nb_bitmap title_bm, body_bm;
	gs_texture_t *title_tex, *body_tex, *title_stroke, *body_stroke;
	int raster_w, body_height;
	double distance; /* 正文需要滚动的像素 */
};

struct nb_board {
	struct nb_cfg cfg;
	int n;
	struct nb_item items[NB_MAX_ITEMS];
	int content_x, content_y, content_w, content_h;
	bool truncated;
};

struct nb_player {
	int slot;
	double t, sx, sy;
};

struct nb_ctx {
	obs_source_t *source;
	SRWLOCK lock;
	struct nb_board *live;
	struct nb_board *draft; /* NULL = 草稿与直播一致 */
	struct nb_player live_pl, draft_pl;
	bool paused;
	gs_effect_t *effect;
	struct nb_bitmap badge_bm;
	int badge_text_w;
	gs_texture_t *badge_tex;
	int width, height;
	SRWLOCK build_lock; /* 串行化画板重建，保证草稿 / 直播比较不会过期 */
	HANDLE worker, wake;
	volatile LONG stop, dirty;
	obs_hotkey_id hk_prev, hk_next, hk_pause, hk_replay;
};

static void cfg_free(struct nb_cfg *c)
{
	for (int i = 0; i < c->n; i++) {
		bfree(c->items[i].title);
		bfree(c->items[i].body);
	}
	c->n = 0;
	dstr_free(&c->sig);
}

static bool blank(const char *s)
{
	if (!s)
		return true;
	for (; *s; s++)
		if (*s != ' ' && *s != '\t' && *s != '\r' && *s != '\n')
			return false;
	return true;
}

static int clampi(long long v, int lo, int hi)
{
	return v < lo ? lo : (v > hi ? hi : (int)v);
}

static double clampd(double v, double lo, double hi)
{
	if (isnan(v))
		return lo;
	return v < lo ? lo : (v > hi ? hi : v);
}

static void read_font(obs_data_t *s, const char *key, struct nb_font *f, int def_size)
{
	obs_data_t *fo = obs_data_get_obj(s, key);
	const char *face = fo ? obs_data_get_string(fo, "face") : NULL;
	int size = fo ? (int)obs_data_get_int(fo, "size") : def_size;
	long long flags = fo ? obs_data_get_int(fo, "flags") : 0;
	memset(f, 0, sizeof(*f));
	if (!face || !*face)
		face = "Microsoft YaHei";
	MultiByteToWideChar(CP_UTF8, 0, face, -1, f->face, 63);
	f->size = clampi(size > 0 ? size : def_size, 8, 200);
	f->bold = (flags & OBS_FONT_BOLD) != 0;
	f->italic = (flags & OBS_FONT_ITALIC) != 0;
	f->underline = (flags & OBS_FONT_UNDERLINE) != 0;
	f->strike = (flags & OBS_FONT_STRIKEOUT) != 0;
	obs_data_release(fo);
}

static void sig_font(struct dstr *d, const struct nb_font *f)
{
	char face[256];
	WideCharToMultiByte(CP_UTF8, 0, f->face, -1, face, sizeof(face), NULL, NULL);
	dstr_catf(d, "font:%s:%d:%d%d%d%d;", face, f->size, f->bold, f->italic, f->underline, f->strike);
}

static void cfg_parse(obs_data_t *s, struct nb_cfg *c)
{
	memset(c, 0, sizeof(*c));
	c->width = clampi(obs_data_get_int(s, "width"), 120, 3840);
	c->height = clampi(obs_data_get_int(s, "height"), 60, 2160);
	c->scrolling = obs_data_get_bool(s, "scrolling");
	c->scroll_axis = obs_data_get_bool(s, "scroll_axis");
	c->speed_x = clampd(obs_data_get_double(s, "speed_x"), -500, 500);
	c->speed_y = clampd(obs_data_get_double(s, "speed_y"), -500, 500);
	c->scroll_limit_width = obs_data_get_bool(s, "scroll_limit_width");
	c->scroll_width = clampi(obs_data_get_int(s, "scroll_width"), 60, 4096);
	c->outline = obs_data_get_bool(s, "outline");
	c->outline_size = clampi(obs_data_get_int(s, "outline_size"), 1, 20);
	c->outline_color = (uint32_t)obs_data_get_int(s, "outline_color");
	c->outline_opacity = clampi(obs_data_get_int(s, "outline_opacity"), 0, 100) / 100.0;
	c->text_opacity = clampi(obs_data_get_int(s, "text_opacity"), 0, 100) / 100.0;
	c->max_height = clampi(obs_data_get_int(s, "scroll_height"), 60, 4096);
	read_font(s, "title_font", &c->title_font, 20);
	read_font(s, "body_font", &c->body_font, 28);
	c->title_font.outline = c->body_font.outline = c->outline ? c->outline_size : 0;
	c->title_color = (uint32_t)obs_data_get_int(s, "title_color");
	c->text_color = (uint32_t)obs_data_get_int(s, "text_color");
	c->bg_color = (uint32_t)obs_data_get_int(s, "bg_color");
	c->border_color = (uint32_t)obs_data_get_int(s, "border_color");
	c->bg_opacity = clampi(obs_data_get_int(s, "bg_opacity"), 0, 100) / 100.0;
	c->border_opacity = clampi(obs_data_get_int(s, "border_opacity"), 0, 100) / 100.0;
	c->border_width = clampi(obs_data_get_int(s, "border_width"), 0, 16);
	c->radius = clampi(obs_data_get_int(s, "radius"), 0, 200);
	c->padding = clampi(obs_data_get_int(s, "padding"), 0, 200);
	c->title_gap = clampi(obs_data_get_int(s, "title_gap"), 0, 200);
	c->line_height = clampd(obs_data_get_double(s, "line_height"), 1.0, 3.0);
	const char *al = obs_data_get_string(s, "align");
	c->align = (al && strcmp(al, "center") == 0) ? NB_ALIGN_CENTER
		   : (al && strcmp(al, "right") == 0) ? NB_ALIGN_RIGHT
						       : NB_ALIGN_LEFT;
	const char *mode = obs_data_get_string(s, "mode");
	c->play.rotate = mode && strcmp(mode, "rotate") == 0;
	c->play.interval = clampd((double)obs_data_get_int(s, "interval"), 2, 3600);
	c->play.speed = clampd((double)obs_data_get_int(s, "speed"), 5, 400);
	c->play.top_dwell = clampd(obs_data_get_double(s, "top_dwell"), 0, 60);
	c->play.bottom_dwell = clampd(obs_data_get_double(s, "bottom_dwell"), 0, 60);
	c->play.fade = clampd(obs_data_get_double(s, "fade"), 0, 5);

	int count = clampi(obs_data_get_int(s, "count"), 1, NB_MAX_ITEMS);
	for (int i = 1; i <= count; i++) {
		char k_on[32], k_title[32], k_body[32];
		snprintf(k_on, sizeof(k_on), "a%d_on", i);
		snprintf(k_title, sizeof(k_title), "a%d_title", i);
		snprintf(k_body, sizeof(k_body), "a%d_body", i);
		if (!obs_data_get_bool(s, k_on))
			continue;
		const char *title = obs_data_get_string(s, k_title);
		const char *body = obs_data_get_string(s, k_body);
		if (blank(title) && blank(body))
			continue;
		struct nb_cfg_item *it = &c->items[c->n++];
		it->slot = i;
		it->title = bstrdup(blank(title) ? "" : title);
		it->body = bstrdup(blank(body) ? "" : body);
	}

	struct dstr *d = &c->sig;
	dstr_catf(d, "v1;%d;%d;%d;%d;", 0, 0, (int)c->scrolling, c->max_height);
	dstr_catf(d, "axis:%d;%.3f;%.3f;%d;%d;outline:%d;%d;%u;%.3f;%.3f;",
		c->scroll_axis, c->speed_x, c->speed_y, c->scroll_limit_width, c->scroll_width,
		c->outline, c->outline_size, c->outline_color, c->outline_opacity, c->text_opacity);
	sig_font(d, &c->title_font);
	sig_font(d, &c->body_font);
	dstr_catf(d, "%u;%u;%u;%u;%.3f;%.3f;%d;%d;%d;%d;%.3f;%d;", c->title_color, c->text_color, c->bg_color,
		  c->border_color, c->bg_opacity, c->border_opacity, c->border_width, c->radius, c->padding,
		  c->title_gap, c->line_height, (int)c->align);
	dstr_catf(d, "%d;%.3f;%.3f;%.3f;%.3f;%.3f;", c->play.rotate, c->play.interval, c->play.speed,
		  c->play.top_dwell, c->play.bottom_dwell, c->play.fade);
	for (int i = 0; i < c->n; i++)
		dstr_catf(d, "[%d|%d:%s|%d:%s]", c->items[i].slot, (int)strlen(c->items[i].title),
			  c->items[i].title, (int)strlen(c->items[i].body), c->items[i].body);
}

/* ---------------------------------------------------------------- 画板（栅格化后的公告） */

static struct nb_board *board_build(obs_data_t *s, bool draft_hint)
{
	uint64_t t0 = os_gettime_ns();
	struct nb_board *b = bzalloc(sizeof(*b));
	cfg_parse(s, &b->cfg);
	struct nb_cfg *c = &b->cfg;
	int inset = c->border_width + c->padding;
	/* Each announcement has its own natural dimensions. Scene-item scale is
	 * entirely owned by OBS; neither selection nor layout ever touches it. */
	b->content_x = b->content_y = inset;
	b->content_w = 16;
	c->width = c->height = 32;
	int n = c->n;
	for (int i = 0; i < n; i++) {
		struct nb_item *it = &b->items[b->n++];
		it->slot = c->items[i].slot;
		int tw = nb_measure_width(c->items[i].title, &c->title_font);
		int bw = nb_measure_width(c->items[i].body, &c->body_font);
		it->width = clampi((tw > bw ? tw : bw) + inset * 2, 32, 4096);
		it->content_w = it->width - inset * 2;
		if (it->content_w < 16) it->content_w = 16;
		nb_raster_text(c->items[i].title, &c->title_font, it->content_w, c->align, 1.35, false, &it->title_bm);
		nb_raster_text(c->items[i].body, &c->body_font, it->content_w, c->align, c->line_height, false,
			       &it->body_bm);
		it->raster_w = it->body_bm.w;
		it->body_height = it->body_bm.h;
		if (c->scrolling && c->scroll_axis && c->scroll_limit_width && it->width > c->scroll_width)
			it->width = c->scroll_width;
		if (it->body_bm.truncated)
			b->truncated = true;
	}
	if (b->n == 0 && draft_hint) {
		struct nb_item *it = &b->items[b->n++];
		it->slot = -1;
		it->width = clampi(nb_measure_width(T("Preview.Empty"), &c->body_font) + inset * 2, 32, 4096);
		it->content_w = it->width - inset * 2;
		nb_raster_text(T("Preview.Empty"), &c->body_font, it->content_w, c->align, c->line_height, false,
			       &it->body_bm);
	}
	for (int i = 0; i < b->n; i++) {
		struct nb_item *it = &b->items[i];
		int title_h = it->title_bm.data ? it->title_bm.h + (it->body_bm.data ? c->title_gap : 0) : 0;
		int need = title_h + it->body_bm.h;
		int cap = c->scrolling ? c->max_height : 4096;
		if (!c->scrolling && need + inset * 2 > cap) b->truncated = true;
		it->height = clampi(need + inset * 2, 32, cap);
		it->content_h = it->height - inset * 2;
		if (it->content_h < 0) it->content_h = 0;
		int view_h = it->content_h - title_h;
		if (view_h < 0) view_h = 0;
		it->distance = c->scrolling && !c->scroll_axis && it->body_bm.h > view_h ? (double)(it->body_bm.h - view_h) : 0.0;
		if (it->width > c->width) c->width = it->width;
		if (it->height > c->height) c->height = it->height;
	}
	blog(LOG_DEBUG, "[公告板] 排版 %d 条用时 %.2f ms", b->n, (os_gettime_ns() - t0) / 1e6);
	return b;
}

/* 必须在图形上下文中调用 */
static void board_upload(struct nb_board *b)
{
	for (int i = 0; i < b->n; i++) {
		struct nb_item *it = &b->items[i];
		if (!it->title_tex && it->title_bm.data) {
			if (it->title_bm.stroke) {
				const uint8_t *st = it->title_bm.stroke;
				it->title_stroke = gs_texture_create(it->title_bm.w, it->title_bm.h, GS_R8, 1, &st, 0);
			}
			const uint8_t *p = it->title_bm.data;
			it->title_tex = gs_texture_create(it->title_bm.w, it->title_bm.h, GS_R8, 1, &p, 0);
			if (it->title_tex)
				nb_bitmap_free(&it->title_bm);
		}
		if (!it->body_tex && it->body_bm.data) {
			if (it->body_bm.stroke) {
				const uint8_t *st = it->body_bm.stroke;
				it->body_stroke = gs_texture_create(it->body_bm.w, it->body_bm.h, GS_R8, 1, &st, 0);
			}
			const uint8_t *p = it->body_bm.data;
			int h = it->body_bm.h;
			it->body_tex = gs_texture_create(it->body_bm.w, h, GS_R8, 1, &p, 0);
			if (it->body_tex)
				nb_bitmap_free(&it->body_bm);
		}
	}
}

/* 纹理上传后位图被释放，高度从纹理取 */
static int item_title_h(const struct nb_item *it)
{
	if (it->title_tex)
		return (int)gs_texture_get_height(it->title_tex);
	return it->title_bm.data ? it->title_bm.h : 0;
}

/* 不能在持有 ctx->lock 时调用（会进入图形上下文） */
static void board_destroy(struct nb_board *b)
{
	if (!b)
		return;
	bool has_tex = false;
	for (int i = 0; i < b->n; i++)
		if (b->items[i].title_tex || b->items[i].body_tex)
			has_tex = true;
	if (has_tex) {
		obs_enter_graphics();
		for (int i = 0; i < b->n; i++) {
			gs_texture_destroy(b->items[i].title_tex);
			gs_texture_destroy(b->items[i].body_tex);
			gs_texture_destroy(b->items[i].title_stroke);
			gs_texture_destroy(b->items[i].body_stroke);
		}
		obs_leave_graphics();
	}
	for (int i = 0; i < b->n; i++) {
		nb_bitmap_free(&b->items[i].title_bm);
		nb_bitmap_free(&b->items[i].body_bm);
	}
	cfg_free(&b->cfg);
	bfree(b);
}

static int board_find(const struct nb_board *b, int slot)
{
	if (!b) return -1;
	for (int i = 0; i < b->n; i++)
		if (b->items[i].slot == slot)
			return i;
	return -1;
}

/* ---------------------------------------------------------------- 播放 */

static void player_fix(const struct nb_board *b, struct nb_player *pl)
{
	if (!b || b->n == 0)
		return;
	if (board_find(b, pl->slot) < 0) {
		/* 当前条被删除/停用：跳到它后面的第一条 */
		int next = 0;
		for (int i = 0; i < b->n; i++)
			if (b->items[i].slot > pl->slot) {
				next = i;
				break;
			}
		pl->slot = b->items[next].slot;
		pl->t = pl->sx = pl->sy = 0;
	}
}

static void player_tick(const struct nb_board *b, struct nb_player *pl, double dt, bool paused)
{
	if (!b || b->n == 0)
		return;
	player_fix(b, pl);
	int idx = board_find(b, pl->slot);
	if (b->cfg.scrolling && b->cfg.scroll_axis && !paused) {
		const struct nb_item *it = &b->items[idx];
		double seconds = clampd(dt, 0, 1);
		pl->sx = b->cfg.speed_x == 0 ? 0 : fmod(pl->sx + b->cfg.speed_x * seconds, it->raster_w > 0 ? it->raster_w : 1);
		pl->sy = b->cfg.speed_y == 0 ? 0 : fmod(pl->sy + b->cfg.speed_y * seconds, it->body_height > 0 ? it->body_height : 1);
	}
	if (nb_advance(&pl->t, dt, b->items[idx].distance, &b->cfg.play, b->n, paused)) {
		idx = (idx + 1) % b->n;
		pl->slot = b->items[idx].slot;
		pl->t = pl->sx = pl->sy = 0;
	}
}

static void player_step(const struct nb_board *b, struct nb_player *pl, int delta)
{
	if (!b || b->n == 0)
		return;
	player_fix(b, pl);
	int idx = board_find(b, pl->slot);
	idx = ((idx + delta) % b->n + b->n) % b->n;
	pl->slot = b->items[idx].slot;
	pl->t = pl->sx = pl->sy = 0;
}

static void player_select(const struct nb_board *b, struct nb_player *pl, int slot)
{
	if (!b || b->n == 0)
		return;
	if (board_find(b, slot) < 0) {
		player_fix(b, pl);
		return;
	}
	if (pl->slot != slot) {
		pl->slot = slot;
		pl->t = pl->sx = pl->sy = 0;
	}
}

/* ---------------------------------------------------------------- 应用 / 草稿 */

static void set_defaults(obs_data_t *s);

/* 用某份设置（草稿或直播快照）重建直播画板 */
static void rebuild_live(struct nb_ctx *ctx, obs_data_t *live_data)
{
	set_defaults(live_data);
	struct nb_board *b = board_build(live_data, false);
	struct nb_board *old;
	AcquireSRWLockExclusive(&ctx->lock);
	old = ctx->live;
	bool same_slot = old && board_find(b, ctx->live_pl.slot) >= 0;
	ctx->live = b;
	ctx->width = b->cfg.width;
	ctx->height = b->cfg.height;
	player_fix(b, &ctx->live_pl);
	/* 应用后同一条公告直接以完整亮度从顶部显示，避免整块闪一下 */
	ctx->live_pl.t = same_slot ? b->cfg.play.fade : 0;
	ctx->live_pl.sx = ctx->live_pl.sy = 0;
	ReleaseSRWLockExclusive(&ctx->lock);
	board_destroy(old);
	if (b->truncated)
		blog(LOG_WARNING, "[公告板] 有公告正文太长，超出显卡纹理上限的部分不会显示");
}

/* 草稿：与直播一致则不生成草稿画板 */
static void refresh_draft(struct nb_ctx *ctx, obs_data_t *settings)
{
	struct nb_cfg probe;
	cfg_parse(settings, &probe);
	bool same;
	AcquireSRWLockShared(&ctx->lock);
	same = ctx->live && dstr_cmp(&ctx->live->cfg.sig, probe.sig.array ? probe.sig.array : "") == 0;
	ReleaseSRWLockShared(&ctx->lock);
	cfg_free(&probe);

	struct nb_board *b = same ? NULL : board_build(settings, true);
	struct nb_board *old;
	AcquireSRWLockExclusive(&ctx->lock);
	old = ctx->draft;
	ctx->draft = b;
	if (b) {
		if (!old)
			ctx->draft_pl = ctx->live_pl;
		player_fix(b, &ctx->draft_pl);
		player_select(b, &ctx->draft_pl, (int)obs_data_get_int(settings, "ui_edit"));
		if (!old || old->cfg.width != b->cfg.width)
			ctx->draft_pl.t = b->cfg.play.fade;
	}
	ReleaseSRWLockExclusive(&ctx->lock);
	board_destroy(old);
}

/* 把一份设置里所有公告板字段的「实际值」（含默认值）完整复制到另一份 */
enum nb_key_type { K_INT, K_BOOL, K_STR, K_DBL, K_OBJ };
static const struct {
	const char *key;
	enum nb_key_type type;
} nb_keys[] = {
	{"count", K_INT},          {"mode", K_STR},          {"interval", K_INT},      {"width", K_INT},
	{"height", K_INT}, {"auto_height", K_BOOL}, {"max_height", K_INT},
	{"scroll_axis", K_BOOL}, {"speed_x", K_DBL}, {"speed_y", K_DBL},
	{"scroll_limit_width", K_BOOL}, {"scroll_width", K_INT},
	{"outline", K_BOOL}, {"outline_size", K_INT}, {"outline_color", K_INT},
	{"outline_opacity", K_INT}, {"text_opacity", K_INT},
	{"scrolling", K_BOOL}, {"scroll_height", K_INT},    {"title_font", K_OBJ},
	{"body_font", K_OBJ},      {"title_color", K_INT},
	{"text_color", K_INT},     {"bg_color", K_INT},      {"bg_opacity", K_INT},    {"align", K_STR},
	{"preset", K_STR}, {"color_preset", K_STR}, {"scheme_preset", K_STR},         {"show_advanced", K_BOOL}, {"border_color", K_INT},  {"border_opacity", K_INT},
	{"border_width", K_INT},
	{"radius", K_INT},         {"padding", K_INT},       {"title_gap", K_INT},     {"line_height", K_DBL},
	{"speed", K_INT},          {"top_dwell", K_DBL},     {"bottom_dwell", K_DBL},  {"fade", K_DBL},
};

static void copy_key(obs_data_t *dst, obs_data_t *src, const char *k, enum nb_key_type t)
{
	switch (t) {
	case K_INT:
		obs_data_set_int(dst, k, obs_data_get_int(src, k));
		break;
	case K_BOOL:
		obs_data_set_bool(dst, k, obs_data_get_bool(src, k));
		break;
	case K_STR:
		obs_data_set_string(dst, k, obs_data_get_string(src, k));
		break;
	case K_DBL:
		obs_data_set_double(dst, k, obs_data_get_double(src, k));
		break;
	case K_OBJ: {
		obs_data_t *o = obs_data_get_obj(src, k);
		if (o) {
			obs_data_t *copy = obs_data_create();
			obs_data_apply(copy, o);
			/* 字体默认值也写成实际值 */
			obs_data_set_string(copy, "face", obs_data_get_string(o, "face"));
			obs_data_set_string(copy, "style", obs_data_get_string(o, "style"));
			obs_data_set_int(copy, "size", obs_data_get_int(o, "size"));
			obs_data_set_int(copy, "flags", obs_data_get_int(o, "flags"));
			obs_data_set_obj(dst, k, copy);
			obs_data_release(copy);
			obs_data_release(o);
		}
		break;
	}
	}
}

static void copy_effective(obs_data_t *dst, obs_data_t *src)
{
	for (size_t i = 0; i < sizeof(nb_keys) / sizeof(nb_keys[0]); i++)
		copy_key(dst, src, nb_keys[i].key, nb_keys[i].type);
	for (int i = 1; i <= NB_MAX_ITEMS; i++) {
		char k[32];
		snprintf(k, sizeof(k), "a%d_on", i);
		copy_key(dst, src, k, K_BOOL);
		snprintf(k, sizeof(k), "a%d_title", i);
		copy_key(dst, src, k, K_STR);
		snprintf(k, sizeof(k), "a%d_body", i);
		copy_key(dst, src, k, K_STR);
	}
}

static void apply_to_live(struct nb_ctx *ctx)
{
	AcquireSRWLockExclusive(&ctx->build_lock);
	obs_data_t *settings = obs_source_get_settings(ctx->source);
	obs_data_t *priv = obs_source_get_private_settings(ctx->source);
	obs_data_t *live = obs_data_create();
	copy_effective(live, settings);
	obs_data_set_obj(priv, "live", live);
	rebuild_live(ctx, live);
	refresh_draft(ctx, settings);
	obs_data_release(live);
	obs_data_release(priv);
	obs_data_release(settings);
	ReleaseSRWLockExclusive(&ctx->build_lock);
	blog(LOG_INFO, "[公告板] '%s' 已应用到直播", obs_source_get_name(ctx->source));
}

/* 从私有设置恢复直播快照；没有快照时（新建来源）把当前设置作为直播内容 */
static void load_live(struct nb_ctx *ctx)
{
	obs_data_t *priv = obs_source_get_private_settings(ctx->source);
	obs_data_t *live = obs_data_get_obj(priv, "live");
	if (live) {
		/* obs_save_source may share nested data with a still-live source.
		 * Own the recovered snapshot before setting defaults or building it. */
		obs_data_t *owned = obs_data_create_from_json(obs_data_get_json_with_defaults(live));
		obs_data_release(live);
		live = owned;
		AcquireSRWLockExclusive(&ctx->lock);
		ctx->live_pl.slot = (int)obs_data_get_int(priv, "cur_slot");
		ctx->live_pl.t = ctx->live_pl.sx = ctx->live_pl.sy = 0;
		ctx->paused = obs_data_get_bool(priv, "paused");
		ReleaseSRWLockExclusive(&ctx->lock);
		AcquireSRWLockExclusive(&ctx->build_lock);
		rebuild_live(ctx, live);
		obs_data_t *settings = obs_source_get_settings(ctx->source);
		refresh_draft(ctx, settings);
		obs_data_release(settings);
		ReleaseSRWLockExclusive(&ctx->build_lock);
		obs_data_release(live);
	} else {
		apply_to_live(ctx);
	}
	obs_data_release(priv);
}

/* ---------------------------------------------------------------- 直播控制 */

enum nb_action { NB_PREV, NB_NEXT, NB_PAUSE_TOGGLE, NB_REPLAY };

static void control(struct nb_ctx *ctx, enum nb_action a)
{
	AcquireSRWLockExclusive(&ctx->lock);
	switch (a) {
	case NB_PREV:
		player_step(ctx->live, &ctx->live_pl, -1);
		break;
	case NB_NEXT:
		player_step(ctx->live, &ctx->live_pl, 1);
		break;
	case NB_PAUSE_TOGGLE:
		ctx->paused = !ctx->paused;
		break;
	case NB_REPLAY:
		ctx->live_pl.t = ctx->live_pl.sx = ctx->live_pl.sy = 0;
		break;
	}
	if (a != NB_PAUSE_TOGGLE && ctx->draft) {
		ctx->draft_pl.slot = ctx->live_pl.slot;
		player_fix(ctx->draft, &ctx->draft_pl);
		ctx->draft_pl.t = ctx->draft_pl.sx = ctx->draft_pl.sy = 0;
	}
	ReleaseSRWLockExclusive(&ctx->lock);
}

static void control_select(struct nb_ctx *ctx, int slot)
{
	AcquireSRWLockExclusive(&ctx->lock);
	int before = ctx->live_pl.slot;
	player_select(ctx->live, &ctx->live_pl, slot);
	if (ctx->draft && ctx->live_pl.slot != before) {
		ctx->draft_pl.slot = ctx->live_pl.slot;
		player_fix(ctx->draft, &ctx->draft_pl);
		ctx->draft_pl.t = ctx->draft_pl.sx = ctx->draft_pl.sy = 0;
	}
	ReleaseSRWLockExclusive(&ctx->lock);
}

static void control_pause_set(struct nb_ctx *ctx, bool paused)
{
	AcquireSRWLockExclusive(&ctx->lock);
	ctx->paused = paused;
	ReleaseSRWLockExclusive(&ctx->lock);
}

static void hk_prev(void *data, obs_hotkey_id id, obs_hotkey_t *hk, bool pressed)
{
	UNUSED_PARAMETER(id);
	UNUSED_PARAMETER(hk);
	if (pressed)
		control(data, NB_PREV);
}
static void hk_next(void *data, obs_hotkey_id id, obs_hotkey_t *hk, bool pressed)
{
	UNUSED_PARAMETER(id);
	UNUSED_PARAMETER(hk);
	if (pressed)
		control(data, NB_NEXT);
}
static void hk_pause(void *data, obs_hotkey_id id, obs_hotkey_t *hk, bool pressed)
{
	UNUSED_PARAMETER(id);
	UNUSED_PARAMETER(hk);
	if (pressed)
		control(data, NB_PAUSE_TOGGLE);
}
static void hk_replay(void *data, obs_hotkey_id id, obs_hotkey_t *hk, bool pressed)
{
	UNUSED_PARAMETER(id);
	UNUSED_PARAMETER(hk);
	if (pressed)
		control(data, NB_REPLAY);
}

/* ---------------------------------------------------------------- 来源回调 */

static const char *nb_get_name(void *unused)
{
	UNUSED_PARAMETER(unused);
	return T("Noticeboard");
}

static void font_default(obs_data_t *s, const char *key, int size, bool bold)
{
	obs_data_t *f = obs_data_create();
	obs_data_set_default_string(f, "face", "Microsoft YaHei");
	obs_data_set_default_string(f, "style", bold ? "Bold" : "Regular");
	obs_data_set_default_int(f, "size", size);
	obs_data_set_default_int(f, "flags", bold ? OBS_FONT_BOLD : 0);
	obs_data_set_default_obj(s, key, f);
	obs_data_release(f);
}

static void set_defaults(obs_data_t *s)
{
	obs_data_set_default_int(s, "count", 1);
	for (int i = 1; i <= NB_MAX_ITEMS; i++) {
		char k[32];
		snprintf(k, sizeof(k), "a%d_on", i);
		obs_data_set_default_bool(s, k, true);
	}
	obs_data_set_default_string(s, "a1_title", T("Default.Title"));
	obs_data_set_default_string(s, "a1_body", T("Default.Body"));
	obs_data_set_default_string(s, "mode", "fixed");
	obs_data_set_default_int(s, "interval", 12);
	obs_data_set_default_int(s, "width", 640);
	obs_data_set_default_int(s, "height", 240);
	obs_data_set_default_bool(s, "auto_height", true);
	obs_data_set_default_int(s, "max_height", 1080);
	obs_data_set_default_bool(s, "scrolling", false);
	/* Legacy speed/top/bottom retain their original meanings. The explicit
	 * axis marker changes only on a user action, never while opening old data. */
	obs_data_set_default_bool(s, "scroll_axis", false);
	obs_data_set_default_double(s, "speed_x", 0);
	obs_data_set_default_double(s, "speed_y", 0);
	obs_data_set_default_bool(s, "scroll_limit_width", false);
	obs_data_set_default_int(s, "scroll_width", 640);
	obs_data_set_default_bool(s, "outline", false);
	obs_data_set_default_int(s, "outline_size", 2);
	obs_data_set_default_int(s, "outline_color", 0xFF000000);
	obs_data_set_default_int(s, "outline_opacity", 100);
	obs_data_set_default_int(s, "text_opacity", 100);
	obs_data_set_default_int(s, "scroll_height", 240);
	obs_data_set_default_int(s, "ui_edit", 1);
	font_default(s, "title_font", 20, true);
	font_default(s, "body_font", 28, false);
	obs_data_set_default_int(s, "title_color", 0xFF70A0E0); /* #e0a070 */
	obs_data_set_default_int(s, "text_color", 0xFFFFFFFF);
	obs_data_set_default_int(s, "bg_color", 0xFF1C1418);    /* #18141c */
	obs_data_set_default_int(s, "bg_opacity", 78);
	obs_data_set_default_string(s, "align", "left");
	obs_data_set_default_string(s, "preset", "warm"); /* retained legacy metadata */
	obs_data_set_default_string(s, "color_preset", "custom");
	obs_data_set_default_string(s, "scheme_preset", "custom");
	obs_data_set_default_bool(s, "show_advanced", false);
	obs_data_set_default_int(s, "border_color", 0xFF70A0E0);
	obs_data_set_default_int(s, "border_opacity", 55);
	obs_data_set_default_int(s, "border_width", 1);
	obs_data_set_default_int(s, "radius", 16);
	obs_data_set_default_int(s, "padding", 24);
	obs_data_set_default_int(s, "title_gap", 12);
	obs_data_set_default_double(s, "line_height", 1.6);
	obs_data_set_default_int(s, "speed", 24);
	obs_data_set_default_double(s, "top_dwell", 2.0);
	obs_data_set_default_double(s, "bottom_dwell", 3.0);
	obs_data_set_default_double(s, "fade", 0.45);
}

static void nb_get_defaults(obs_data_t *s)
{
	set_defaults(s);
}

/* 草稿排版放到后台线程：OBS 会在图形线程里调用 update，不能在那里做文字排版拖慢画面 */
static DWORD WINAPI worker_main(LPVOID param)
{
	struct nb_ctx *ctx = param;
	for (;;) {
		WaitForSingleObject(ctx->wake, INFINITE);
		if (InterlockedCompareExchange(&ctx->stop, 0, 0))
			break;
		bool dirty = InterlockedExchange(&ctx->dirty, 0);
		if (dirty) {
			AcquireSRWLockExclusive(&ctx->build_lock);
			obs_data_t *settings = obs_source_get_settings(ctx->source);
			refresh_draft(ctx, settings);
			obs_data_release(settings);
			ReleaseSRWLockExclusive(&ctx->build_lock);
		}
	}
	return 0;
}

static void *nb_create(obs_data_t *settings, obs_source_t *source)
{
	struct nb_ctx *ctx = bzalloc(sizeof(*ctx));
	ctx->source = source;
	InitializeSRWLock(&ctx->lock);
	InitializeSRWLock(&ctx->build_lock);
	ctx->width = 640;
	ctx->height = 240;

	char *path = obs_module_file("noticeboard.effect");
	obs_enter_graphics();
	char *err = NULL;
	ctx->effect = path ? gs_effect_create_from_file(path, &err) : NULL;
	obs_leave_graphics();
	if (!ctx->effect)
		blog(LOG_ERROR, "[公告板] 着色器加载失败：%s (%s)", path ? path : "(找不到 noticeboard.effect)",
		     err ? err : "");
	bfree(err);
	bfree(path);

	struct nb_font bf = {L"Microsoft YaHei", 14, true, false, false, false, 0};
	nb_raster_text(T("Preview.Badge"), &bf, 600, NB_ALIGN_LEFT, 1.35, true, &ctx->badge_bm);
	ctx->badge_text_w = ctx->badge_bm.text_w;

	ctx->hk_prev = obs_hotkey_register_source(source, "noticeboard.prev", T("Hotkey.Prev"), hk_prev, ctx);
	ctx->hk_next = obs_hotkey_register_source(source, "noticeboard.next", T("Hotkey.Next"), hk_next, ctx);
	ctx->hk_pause = obs_hotkey_register_source(source, "noticeboard.pause", T("Hotkey.Pause"), hk_pause, ctx);
	ctx->hk_replay = obs_hotkey_register_source(source, "noticeboard.replay", T("Hotkey.Replay"), hk_replay, ctx);

	UNUSED_PARAMETER(settings);
	/* 加载场景集合时，真正的私有设置在 create 之后才填入，会在 load 回调里重新读取 */
	load_live(ctx);

	ctx->wake = CreateEventW(NULL, FALSE, FALSE, NULL);
	ctx->worker = ctx->wake ? CreateThread(NULL, 0, worker_main, ctx, 0, NULL) : NULL;
	return ctx;
}

static void nb_destroy(void *data)
{
	struct nb_ctx *ctx = data;
	if (ctx->worker) {
		InterlockedExchange(&ctx->stop, 1);
		SetEvent(ctx->wake);
		WaitForSingleObject(ctx->worker, INFINITE);
		CloseHandle(ctx->worker);
	}
	if (ctx->wake)
		CloseHandle(ctx->wake);
	board_destroy(ctx->live);
	board_destroy(ctx->draft);
	obs_enter_graphics();
	gs_effect_destroy(ctx->effect);
	gs_texture_destroy(ctx->badge_tex);
	obs_leave_graphics();
	nb_bitmap_free(&ctx->badge_bm);
	bfree(ctx);
}

static void nb_update(void *data, obs_data_t *settings)
{
	UNUSED_PARAMETER(settings);
	struct nb_ctx *ctx = data;
	if (ctx->worker) {
		InterlockedExchange(&ctx->dirty, 1);
		SetEvent(ctx->wake);
	} else {
		AcquireSRWLockExclusive(&ctx->build_lock);
		refresh_draft(ctx, settings);
		ReleaseSRWLockExclusive(&ctx->build_lock);
	}
}

static void nb_load(void *data, obs_data_t *settings)
{
	UNUSED_PARAMETER(settings);
	load_live(data);
}

static void nb_save(void *data, obs_data_t *settings)
{
	UNUSED_PARAMETER(settings);
	struct nb_ctx *ctx = data;
	obs_data_t *priv = obs_source_get_private_settings(ctx->source);
	AcquireSRWLockShared(&ctx->lock);
	int slot = ctx->live_pl.slot;
	bool paused = ctx->paused;
	ReleaseSRWLockShared(&ctx->lock);
	obs_data_set_int(priv, "cur_slot", slot);
	obs_data_set_bool(priv, "paused", paused);
	obs_data_release(priv);
}

static uint32_t nb_get_width(void *data)
{
	struct nb_ctx *ctx = data;
	AcquireSRWLockShared(&ctx->lock);
	int idx = board_find(ctx->live, ctx->live_pl.slot);
	uint32_t w = idx >= 0 ? (uint32_t)ctx->live->items[idx].width : 32;
	ReleaseSRWLockShared(&ctx->lock);
	return w;
}

static uint32_t nb_get_height(void *data)
{
	struct nb_ctx *ctx = data;
	AcquireSRWLockShared(&ctx->lock);
	int idx = board_find(ctx->live, ctx->live_pl.slot);
	uint32_t h = idx >= 0 ? (uint32_t)ctx->live->items[idx].height : 32;
	ReleaseSRWLockShared(&ctx->lock);
	return h;
}

static void nb_tick(void *data, float seconds)
{
	struct nb_ctx *ctx = data;
	AcquireSRWLockExclusive(&ctx->lock);
	player_tick(ctx->live, &ctx->live_pl, seconds, ctx->paused);
	player_tick(ctx->draft, &ctx->draft_pl, seconds, ctx->paused);
	ReleaseSRWLockExclusive(&ctx->lock);
}

/* ---------------------------------------------------------------- 绘制 */

static void color_vec(struct vec4 *v, uint32_t c, double opacity)
{
	v->x = (float)(c & 0xFF) / 255.0f;
	v->y = (float)((c >> 8) & 0xFF) / 255.0f;
	v->z = (float)((c >> 16) & 0xFF) / 255.0f;
	v->w = (float)opacity;
}

static void draw_card(gs_effect_t *e, float x, float y, float w, float h, const struct vec4 *fill,
		      const struct vec4 *border, float bw, float radius)
{
	struct vec2 size;
	vec2_set(&size, w, h);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "size"), &size);
	gs_effect_set_vec4(gs_effect_get_param_by_name(e, "fill_color"), fill);
	gs_effect_set_vec4(gs_effect_get_param_by_name(e, "border_color"), border);
	gs_effect_set_float(gs_effect_get_param_by_name(e, "border_width"), bw);
	gs_effect_set_float(gs_effect_get_param_by_name(e, "radius"), radius);
	gs_matrix_push();
	gs_matrix_translate3f(x, y, 0.0f);
	while (gs_effect_loop(e, "Card"))
		gs_draw_sprite(NULL, 0, (uint32_t)w, (uint32_t)h);
	gs_matrix_pop();
}

static void draw_region(gs_effect_t *e, gs_texture_t *tex, float x, float y,
	float src_x, float src_y, float vis_w, float vis_h, bool loop, const struct vec4 *color)
{
	if (!tex || vis_w <= 0.5f || vis_h <= 0.5f) return;
	float tw = (float)gs_texture_get_width(tex), th = (float)gs_texture_get_height(tex);
	struct vec2 scale, offset;
	vec2_set(&scale, vis_w/tw, vis_h/th);
	vec2_set(&offset, src_x/tw, src_y/th);
	gs_effect_set_texture(gs_effect_get_param_by_name(e, "image"), tex);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "uv_scale"), &scale);
	gs_effect_set_vec2(gs_effect_get_param_by_name(e, "uv_offset"), &offset);
	gs_effect_set_vec4(gs_effect_get_param_by_name(e, "text_color"), color);
	gs_matrix_push(); gs_matrix_translate3f(x, y, 0);
	while (gs_effect_loop(e, loop ? "TextScroll" : "Text")) gs_draw_quadf(tex, 0, vis_w, vis_h);
	gs_matrix_pop();
}

static void nb_render(void *data, gs_effect_t *unused)
{
	UNUSED_PARAMETER(unused);
	struct nb_ctx *ctx = data;
	gs_effect_t *e = ctx->effect;
	if (!e)
		return;

	/* 直接画到窗口（属性预览、工作室模式预览）时才显示草稿；推流/录像/转场永远是纹理目标 */
	bool to_window = gs_get_render_target() == NULL;

	AcquireSRWLockShared(&ctx->lock);
	struct nb_board *b = ctx->live;
	struct nb_player pl = ctx->live_pl;
	bool draft = false;
	if (to_window && ctx->draft) {
		b = ctx->draft;
		pl = ctx->draft_pl;
		draft = true;
	}
	if (!b || b->n == 0) {
		ReleaseSRWLockShared(&ctx->lock);
		return;
	}
	board_upload(b);
	if (draft && !ctx->badge_tex && ctx->badge_bm.data) {
		const uint8_t *p = ctx->badge_bm.data;
		ctx->badge_tex = gs_texture_create(ctx->badge_bm.w, ctx->badge_bm.h, GS_R8, 1, &p, 0);
		if (ctx->badge_tex)
			nb_bitmap_free(&ctx->badge_bm);
	}

	const struct nb_cfg *c = &b->cfg;
	int idx = board_find(b, pl.slot);
	if (idx < 0)
		idx = 0;
	const struct nb_item *it = &b->items[idx];
	struct nb_frame fr = nb_frame_for(pl.t, it->distance, &c->play, b->n);

	const bool prev_srgb = gs_framebuffer_srgb_enabled();
	gs_enable_framebuffer_srgb(false);
	gs_blend_state_push();
	gs_blend_function_separate(GS_BLEND_SRCALPHA, GS_BLEND_INVSRCALPHA, GS_BLEND_ONE, GS_BLEND_INVSRCALPHA);

	struct vec4 fill, border, tcol, bcol, ocol;
	color_vec(&fill, c->bg_color, c->bg_opacity);
	color_vec(&border, c->border_color, c->border_opacity);
	draw_card(e, 0, 0, (float)it->width, (float)it->height, &fill, &border, (float)c->border_width,
		  (float)c->radius);

	color_vec(&tcol, c->title_color, fr.opacity*c->text_opacity);
	color_vec(&ocol, c->outline_color, fr.opacity*c->outline_opacity);
	color_vec(&bcol, c->text_color, fr.opacity*c->text_opacity);
	float y = (float)b->content_y;
	float avail = (float)it->content_h;
	int th = item_title_h(it);
	if (th > 0) {
		float vis = (float)th < avail ? (float)th : avail;
		float vw = (float)(it->width - b->content_x*2);
		draw_region(e, it->title_stroke, b->content_x, y, 0, 0, vw, vis, false, &ocol);
		draw_region(e, it->title_tex, b->content_x, y, 0, 0, vw, vis, false, &tcol);
		y += (float)(th + (it->body_tex ? c->title_gap : 0));
		avail -= (float)(th + (it->body_tex ? c->title_gap : 0));
	}
	if (it->body_tex && avail > 0) {
		float bh = (float)gs_texture_get_height(it->body_tex);
		bool axis = c->scrolling && c->scroll_axis;
		float off = axis ? (float)pl.sy : (float)fr.offset;
		float vis = axis ? (bh < avail ? bh : avail) : (bh-off < avail ? bh-off : avail);
		float vw = (float)(it->width - b->content_x*2);
		float ox = axis ? (float)pl.sx : 0;
		draw_region(e, it->body_stroke, b->content_x, y, ox, off, vw, vis, axis, &ocol);
		draw_region(e, it->body_tex, b->content_x, y, ox, off, vw, vis, axis, &bcol);
	}

	if (draft && ctx->badge_tex) {
		/* 「草稿预览」角标（右上角） */
		gs_texture_t *tex = ctx->badge_tex;
		float txw = (float)gs_texture_get_width(tex);
		float txh = (float)gs_texture_get_height(tex);
		float text_w = (float)(ctx->badge_text_w > 0 ? ctx->badge_text_w : 120);
		float bw = text_w + 20.0f, bh = txh + 6.0f;
		float bx = (float)it->width - bw - 8.0f, by = 8.0f;
		if (bx < 0)
			bx = 0;
		struct vec4 pill, none, dark;
		color_vec(&pill, 0x70A0E0, 0.92);
		color_vec(&none, 0, 0);
		color_vec(&dark, 0x1C1418, 1.0);
		draw_card(e, bx, by, bw, bh, &pill, &none, 0, bh / 2);
		struct vec2 scale, offset;
		vec2_set(&scale, text_w / txw, 1.0f);
		vec2_set(&offset, 0, 0);
		gs_effect_set_texture(gs_effect_get_param_by_name(e, "image"), tex);
		gs_effect_set_vec2(gs_effect_get_param_by_name(e, "uv_scale"), &scale);
		gs_effect_set_vec2(gs_effect_get_param_by_name(e, "uv_offset"), &offset);
		gs_effect_set_vec4(gs_effect_get_param_by_name(e, "text_color"), &dark);
		gs_matrix_push();
		gs_matrix_translate3f(bx + 10.0f, by + 3.0f, 0);
		while (gs_effect_loop(e, "Text"))
			gs_draw_quadf(tex, 0, text_w, txh);
		gs_matrix_pop();
	}

	gs_blend_state_pop();
	gs_enable_framebuffer_srgb(prev_srgb);
	ReleaseSRWLockShared(&ctx->lock);
}

/* ---------------------------------------------------------------- 属性窗口 */

#define NB_BGR(r, g, b) (0xFF000000u | ((uint32_t)(b) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(r))

struct nb_preset {
	const char *id;
	const char *locale;
	uint32_t title, text, bg, border;
	int bg_op, border_op, border_w, radius, padding, title_gap;
	const char *face;
	int body_size, title_size;
	bool bold;
	int stroke, text_op;
	double line_height;
};

static const struct nb_preset nb_presets[] = {
	{"warm", "Preset.Warm", NB_BGR(0xE0, 0xA0, 0x70), NB_BGR(0xFF, 0xFF, 0xFF), NB_BGR(0x18, 0x14, 0x1C),
	 NB_BGR(0xE0, 0xA0, 0x70), 78, 55, 1, 16, 24, 12, "Microsoft YaHei", 28, 20, false, 0, 100, 1.6},
	{"ivory", "Preset.Ivory", NB_BGR(0x8A, 0x55, 0x38), NB_BGR(0x2A, 0x26, 0x24), NB_BGR(0xF7, 0xF4, 0xEF),
	 NB_BGR(0xC8, 0xB8, 0xA4), 90, 80, 1, 18, 26, 12, "Microsoft YaHei", 28, 20, false, 0, 100, 1.6},
	{"sakura", "Preset.Sakura", NB_BGR(0xC4, 0x5C, 0x7A), NB_BGR(0x4A, 0x30, 0x38), NB_BGR(0xFF, 0xF0, 0xF4),
	 NB_BGR(0xF0, 0xB8, 0xC8), 88, 75, 1, 18, 26, 12, "Microsoft YaHei", 28, 20, false, 0, 100, 1.6},
	{"ink", "Preset.Ink", NB_BGR(0x7E, 0xC8, 0xC0), NB_BGR(0xE8, 0xF0, 0xF0), NB_BGR(0x10, 0x18, 0x1C),
	 NB_BGR(0x3D, 0x8A, 0x84), 80, 60, 1, 16, 24, 12, "Microsoft YaHei", 28, 20, false, 0, 100, 1.6},
	{"glass", "Preset.Glass", NB_BGR(0xF0, 0xE6, 0xD8), NB_BGR(0xFF, 0xFF, 0xFF), NB_BGR(0x12, 0x10, 0x18),
	 NB_BGR(0xFF, 0xFF, 0xFF), 42, 28, 1, 20, 26, 12, "Microsoft YaHei", 28, 20, false, 0, 100, 1.6},
	{"violet", "Preset.Violet", NB_BGR(0xC8, 0xB0, 0xF0), NB_BGR(0xF2, 0xEE, 0xFC), NB_BGR(0x16, 0x14, 0x1F),
	 NB_BGR(0x8A, 0x74, 0xC8), 80, 55, 1, 16, 24, 12, "Microsoft YaHei", 28, 20, false, 0, 100, 1.6},
	{"game", "Preset.Game", NB_BGR(255,210,90), NB_BGR(255,255,255), NB_BGR(12,16,24),
	 NB_BGR(0,0,0), 28, 0, 0, 6, 10, 6, "Microsoft YaHei UI", 24, 18, true, 2, 100, 1.25},
	{"chat", "Preset.Chat", NB_BGR(125,220,190), NB_BGR(245,250,248), NB_BGR(18,28,26),
	 NB_BGR(105,170,150), 72, 30, 1, 12, 18, 10, "DengXian", 26, 18, false, 0, 100, 1.5},
	{"caption", "Preset.Caption", NB_BGR(255,255,255), NB_BGR(255,255,255), NB_BGR(0,0,0),
	 NB_BGR(0,0,0), 58, 0, 0, 0, 12, 4, "SimHei", 36, 22, true, 2, 100, 1.2},
	{"alert", "Preset.Alert", NB_BGR(255,230,150), NB_BGR(255,255,255), NB_BGR(100,20,16),
	 NB_BGR(255,190,80), 96, 100, 3, 8, 20, 10, "Microsoft YaHei", 40, 28, true, 2, 100, 1.3},
	{"quiet", "Preset.Quiet", NB_BGR(235,240,245), NB_BGR(235,240,245), NB_BGR(0,0,0),
	 NB_BGR(0,0,0), 0, 0, 0, 0, 6, 4, "Microsoft YaHei UI", 22, 16, false, 1, 72, 1.3},
	{"note", "Preset.Note", NB_BGR(95,65,35), NB_BGR(65,45,28), NB_BGR(255,246,205),
	 NB_BGR(195,160,100), 95, 45, 1, 3, 22, 12, "KaiTi", 30, 22, false, 0, 100, 1.55},

};

static const struct nb_preset *preset_by_id(const char *id)
{
	if (!id || !id[0])
		return NULL;
	for (size_t i = 0; i < sizeof(nb_presets) / sizeof(nb_presets[0]); i++)
		if (strcmp(nb_presets[i].id, id) == 0)
			return &nb_presets[i];
	return NULL;
}

/* Orthogonal presets (§2.1): colors own RGB values ONLY; schemes own
 * typography, opacity, outline geometry and layout, never colors or playback.
 * The two native dropdowns have independent identities and custom signatures. */
static void apply_colors(obs_data_t *s, const struct nb_preset *pr)
{
	obs_data_set_int(s, "title_color", pr->title);
	obs_data_set_int(s, "text_color", pr->text);
	obs_data_set_int(s, "bg_color", pr->bg);
	obs_data_set_int(s, "border_color", pr->border);
	obs_data_set_int(s, "outline_color", 0xFF000000);
	obs_data_set_string(s, "color_preset", pr->id);
}

static void apply_scheme(obs_data_t *s, const struct nb_preset *pr)
{
	obs_data_set_int(s, "bg_opacity", pr->bg_op);
	obs_data_set_int(s, "border_opacity", pr->border_op);
	obs_data_set_int(s, "border_width", pr->border_w);
	obs_data_set_int(s, "radius", pr->radius);
	obs_data_set_int(s, "padding", pr->padding);
	obs_data_set_int(s, "title_gap", pr->title_gap);
	obs_data_t *font = obs_data_create();
	obs_data_set_string(font, "face", pr->face);
	obs_data_set_string(font, "style", pr->bold ? "Bold" : "Regular");
	obs_data_set_int(font, "size", pr->body_size);
	obs_data_set_int(font, "flags", pr->bold ? OBS_FONT_BOLD : 0);
	obs_data_set_obj(s, "body_font", font);
	obs_data_set_int(font, "size", pr->title_size);
	obs_data_set_int(font, "flags", OBS_FONT_BOLD);
	obs_data_set_string(font, "style", "Bold");
	obs_data_set_obj(s, "title_font", font);
	obs_data_release(font);
	/* obs_data_set_obj retains objects, so body/title need distinct copies. */
	font = obs_data_get_obj(s, "body_font");
	obs_data_t *body = obs_data_create();
	obs_data_apply(body, font);
	obs_data_set_int(body, "size", pr->body_size);
	obs_data_set_int(body, "flags", pr->bold ? OBS_FONT_BOLD : 0);
	obs_data_set_string(body, "style", pr->bold ? "Bold" : "Regular");
	obs_data_set_obj(s, "body_font", body);
	obs_data_release(body); obs_data_release(font);
	obs_data_set_bool(s, "outline", pr->stroke > 0);
	obs_data_set_int(s, "outline_size", pr->stroke > 0 ? pr->stroke : 2);
	obs_data_set_int(s, "outline_opacity", 100);
	obs_data_set_int(s, "text_opacity", pr->text_op);
	obs_data_set_double(s, "line_height", pr->line_height);
	obs_data_set_string(s, "align", "left");
	obs_data_set_string(s, "scheme_preset", pr->id);
}

static void item_copy(obs_data_t *s, int from, int to)
{
	char k[32], d[32];
	snprintf(k, sizeof(k), "a%d_on", from);
	snprintf(d, sizeof(d), "a%d_on", to);
	obs_data_set_bool(s, d, obs_data_get_bool(s, k));
	snprintf(k, sizeof(k), "a%d_title", from);
	snprintf(d, sizeof(d), "a%d_title", to);
	obs_data_set_string(s, d, obs_data_get_string(s, k));
	snprintf(k, sizeof(k), "a%d_body", from);
	snprintf(d, sizeof(d), "a%d_body", to);
	obs_data_set_string(s, d, obs_data_get_string(s, k));
}

static void item_clear(obs_data_t *s, int i)
{
	char k[32];
	snprintf(k, sizeof(k), "a%d_on", i);
	obs_data_set_bool(s, k, true);
	snprintf(k, sizeof(k), "a%d_title", i);
	obs_data_set_string(s, k, "");
	snprintf(k, sizeof(k), "a%d_body", i);
	obs_data_set_string(s, k, "");
}

/* Native OBS properties have no text-editor height/row API. Keep one selected
 * announcement visible (hierarchy §2.1 / spacing §3.6), with a one-line escaped
 * body by default and an optional native multiline editor. No Qt dependency.
 * Primary action: apply; secondary: select/edit/add; advanced: playback/style.
 * All metrics/colors/theme/keyboard behavior belong to OBS, not custom widgets. */
struct nb_ui {
	struct nb_ctx *ctx;
	obs_data_t *settings; /* exact view settings, NOT a delayed source copy */
	char color_preset[32], scheme_preset[32];
	bool last_scrolling;
	struct dstr color_sig, scheme_sig, structure_sig;
};

static void ui_free(void *data)
{
	struct nb_ui *ui = data;
	obs_data_release(ui->settings);
	dstr_free(&ui->color_sig);
	dstr_free(&ui->scheme_sig);
	dstr_free(&ui->structure_sig);
	bfree(ui);
}

static void encode_body(obs_data_t *s, int slot)
{
	char raw[32], compact[32];
	snprintf(raw, sizeof(raw), "a%d_body", slot);
	snprintf(compact, sizeof(compact), "ui_body%d", slot);
	struct dstr d = {0};
	for (const char *c = obs_data_get_string(s, raw); *c; c++) {
		if (*c == '\n') dstr_cat(&d, "\\n");
		else if (*c == '\r') dstr_cat(&d, "\\r");
		else if (*c == '\\') dstr_cat(&d, "\\\\");
		else dstr_ncat(&d, c, 1);
	}
	obs_data_set_string(s, compact, d.array ? d.array : "");
	dstr_free(&d);
}

static void color_signature(obs_data_t *s, struct dstr *d)
{
	static const char *keys[] = {"title_color", "text_color", "bg_color", "border_color", "outline_color"};
	dstr_copy(d, "");
	for (size_t i = 0; i < sizeof(keys)/sizeof(keys[0]); i++)
		dstr_catf(d, "%s:%u;", keys[i], (unsigned)obs_data_get_int(s, keys[i]) & 0xFFFFFFu);
}

static void scheme_signature(obs_data_t *s, struct dstr *d)
{
	static const char *ints[] = {"text_opacity", "bg_opacity", "outline", "outline_size", "outline_opacity",
		"border_opacity", "border_width", "radius", "padding", "title_gap"};
	struct nb_font f;
	dstr_copy(d, "");
	read_font(s, "title_font", &f, 20); sig_font(d, &f);
	read_font(s, "body_font", &f, 28); sig_font(d, &f);
	for (size_t i = 0; i < sizeof(ints)/sizeof(ints[0]); i++)
		dstr_catf(d, "%s:%lld;", ints[i], strcmp(ints[i], "outline") == 0 ?
			(long long)obs_data_get_bool(s, ints[i]) : obs_data_get_int(s, ints[i]));
	dstr_catf(d, "%.4f;%s", obs_data_get_double(s, "line_height"), obs_data_get_string(s, "align"));
}

/* Read-only style migration: infer a label from actual values, NEVER reapply
 * a saved legacy preset. 1.3/custom 1.4 styles must remain byte-for-byte intact. */
static void migrate_preset_labels(obs_data_t *s)
{
	bool color_missing = !obs_data_has_user_value(s, "color_preset");
	bool scheme_missing = !obs_data_has_user_value(s, "scheme_preset");
	if (!color_missing && !scheme_missing) return;
	struct dstr colors = {0}, scheme = {0};
	color_signature(s, &colors); scheme_signature(s, &scheme);
	const char *color_id = "custom", *scheme_id = "custom";
	for (size_t i = 0; i < sizeof(nb_presets)/sizeof(nb_presets[0]); i++) {
		obs_data_t *probe = obs_data_create();
		set_defaults(probe);
		struct dstr signature = {0};
		if (i < 6) {
			apply_colors(probe, &nb_presets[i]); color_signature(probe, &signature);
			if (dstr_cmp(&signature, colors.array) == 0) color_id = nb_presets[i].id;
		} else {
			apply_scheme(probe, &nb_presets[i]); scheme_signature(probe, &signature);
			if (dstr_cmp(&signature, scheme.array) == 0) scheme_id = nb_presets[i].id;
		}
		dstr_free(&signature); obs_data_release(probe);
	}
	if (color_missing) obs_data_set_string(s, "color_preset", color_id);
	if (scheme_missing) obs_data_set_string(s, "scheme_preset", scheme_id);
	dstr_free(&colors); dstr_free(&scheme);
}

static void ui_capture_presets(struct nb_ui *ui, obs_data_t *s)
{
	migrate_preset_labels(s);
	snprintf(ui->color_preset, sizeof(ui->color_preset), "%s", obs_data_get_string(s, "color_preset"));
	snprintf(ui->scheme_preset, sizeof(ui->scheme_preset), "%s", obs_data_get_string(s, "scheme_preset"));
	color_signature(s, &ui->color_sig); scheme_signature(s, &ui->scheme_sig);
}

/* This signature describes actual dependent-control visibility, not draft
 * values. A normal edit must NEVER ask OBSPropertiesView to destroy/recreate
 * widgets (properties-view.cpp:2137), even its first edit of a named preset.
 * Cosmetic labels/status may catch up at the next deliberate structural action. */
static void structure_signature(obs_data_t *s, struct dstr *d)
{
	dstr_printf(d, "%lld;%d;%d;%d;%d;%d;%d;%d;",
		obs_data_get_int(s, "ui_edit"), obs_data_get_bool(s, "ui_multiline"),
		obs_data_get_bool(s, "show_advanced"), obs_data_get_bool(s, "outline"),
		obs_data_get_bool(s, "scrolling"), obs_data_get_bool(s, "scroll_axis"),
		obs_data_get_bool(s, "scroll_limit_width"),
		strcmp(obs_data_get_string(s, "mode"), "rotate") == 0);
}

static void ui_bind(obs_properties_t *props, obs_data_t *s)
{
	struct nb_ui *ui = obs_properties_get_param(props);
	if (!ui || ui->settings == s) return;
	obs_data_addref(s);
	obs_data_release(ui->settings);
	ui->settings = s;
	for (int i = 1; i <= NB_MAX_ITEMS; i++) encode_body(s, i);
	ui_capture_presets(ui, s);
	structure_signature(s, &ui->structure_sig);
	ui->last_scrolling = obs_data_get_bool(s, "scrolling");
	obs_data_set_double(s, "ui_last_x", obs_data_get_double(s, "speed_x"));
	obs_data_set_double(s, "ui_last_y", obs_data_get_double(s, "speed_y"));
	if (ui->ctx) {
		AcquireSRWLockShared(&ui->ctx->lock);
		obs_data_set_int(s, "ui_current", ui->ctx->live_pl.slot);
		obs_data_set_bool(s, "ui_paused", ui->ctx->paused);
		ReleaseSRWLockShared(&ui->ctx->lock);
	}
}

static void ui_sync(obs_properties_t *props, obs_data_t *s)
{
	struct nb_ui *ui = obs_properties_get_param(props);
	int count = clampi(obs_data_get_int(s, "count"), 1, NB_MAX_ITEMS);
	int edit = clampi(obs_data_get_int(s, "ui_edit"), 1, count);
	obs_data_set_int(s, "ui_edit", edit);
	if (ui) structure_signature(s, &ui->structure_sig);
	obs_property_t *sel = obs_properties_get(props, "ui_edit");
	obs_property_list_clear(sel);
	for (int i = 1; i <= NB_MAX_ITEMS; i++) {
		char k[32], label[256];
		snprintf(k, sizeof(k), "a%d_title", i);
		const char *title = obs_data_get_string(s, k);
		if (i <= count) {
			snprintf(label, sizeof(label), T("Current.Item"), i, *title ? title : T("Status.Untitled"));
			obs_property_list_add_int(sel, label, i);
		}
		snprintf(k, sizeof(k), "grp_a%d", i);
		obs_property_set_visible(obs_properties_get(props, k), i == edit);
		snprintf(k, sizeof(k), "a%d_body", i);
		obs_property_set_visible(obs_properties_get(props, k), obs_data_get_bool(s, "ui_multiline"));
		snprintf(k, sizeof(k), "ui_body%d", i);
		obs_property_set_visible(obs_properties_get(props, k), !obs_data_get_bool(s, "ui_multiline"));
	}
	obs_property_set_enabled(obs_properties_get(props, "add_item"), count < NB_MAX_ITEMS);
	obs_property_set_enabled(obs_properties_get(props, "delete_item"), count > 1);
	obs_property_set_visible(obs_properties_get(props, "grp_adv"), obs_data_get_bool(s, "show_advanced"));
	bool scroll = obs_data_get_bool(s, "scrolling"), axis = obs_data_get_bool(s, "scroll_axis");
	obs_property_set_visible(obs_properties_get(props, "scroll_height"), scroll);
	obs_property_set_visible(obs_properties_get(props, "speed_x"), scroll);
	obs_property_set_visible(obs_properties_get(props, "speed_y"), scroll);
	obs_property_set_visible(obs_properties_get(props, "legacy_scroll_info"), scroll && !axis);
	obs_property_set_visible(obs_properties_get(props, "speed"), scroll && !axis);
	obs_property_set_visible(obs_properties_get(props, "top_dwell"), scroll && !axis);
	obs_property_set_visible(obs_properties_get(props, "bottom_dwell"), scroll && !axis);
	obs_property_set_visible(obs_properties_get(props, "scroll_limit_width"), scroll && axis);
	obs_property_set_visible(obs_properties_get(props, "scroll_width"), scroll && axis && obs_data_get_bool(s, "scroll_limit_width"));
	const char *outline_fields[] = {"outline_size", "outline_color", "outline_opacity"};
	for (int i = 0; i < 3; i++) obs_property_set_visible(obs_properties_get(props, outline_fields[i]), obs_data_get_bool(s, "outline"));
	obs_property_set_visible(obs_properties_get(props, "interval"), strcmp(obs_data_get_string(s, "mode"), "rotate") == 0);
	if (!ui || !ui->ctx) return;
	struct nb_ctx *ctx = ui->ctx;
	obs_property_t *cur = obs_properties_get(props, "ui_current");
	obs_property_list_clear(cur);
	struct nb_cfg probe;
	cfg_parse(s, &probe);
	AcquireSRWLockShared(&ctx->lock);
	struct nb_board *b = ctx->live;
	if (b) for (int i = 0; i < b->cfg.n; i++) {
		char label[256];
		struct nb_cfg_item *it = &b->cfg.items[i];
		snprintf(label, sizeof(label), T("Current.Item"), it->slot, *it->title ? it->title : T("Status.Untitled"));
		obs_property_list_add_int(cur, label, it->slot);
	}
	bool dirty = !b || dstr_cmp(&b->cfg.sig, probe.sig.array ? probe.sig.array : "") != 0;
	obs_data_set_int(s, "ui_current", ctx->live_pl.slot);
	obs_data_set_bool(s, "ui_paused", ctx->paused);
	ReleaseSRWLockShared(&ctx->lock);
	cfg_free(&probe);
	obs_property_set_enabled(cur, obs_property_list_item_count(cur) > 0);
	obs_property_set_description(obs_properties_get(props, "status"), T(dirty ? "Status.DraftDirty" : "Status.DraftClean"));
}

/* Capture exact property-view settings, independent of native visual updates
 * and the render worker, so typing then immediately Add/Apply loses no edits. */
static bool ui_modified(obs_properties_t *props, obs_property_t *p, obs_data_t *s)
{
	ui_bind(props, s);
	const char *name = obs_property_name(p);
	struct nb_ui *ui = obs_properties_get_param(props);
	bool refresh = false;
	int slot = 0;
	if (sscanf(name, "ui_body%d", &slot) == 1 && slot >= 1 && slot <= NB_MAX_ITEMS) {
		char key[32]; snprintf(key, sizeof(key), "a%d_body", slot);
		struct dstr d = {0};
		for (const char *c = obs_data_get_string(s, name); *c; c++) {
			if (*c == '\\' && c[1] && (c[1] == 'n' || c[1] == 'r' || c[1] == '\\')) {
				c++; char v = *c == 'n' ? '\n' : *c == 'r' ? '\r' : '\\';
				dstr_ncat(&d, &v, 1);
			} else dstr_ncat(&d, c, 1);
		}
		obs_data_set_string(s, key, d.array ? d.array : "");
		dstr_free(&d);
	} else if (name[0] == 'a' && sscanf(name, "a%d", &slot) == 1) {
		if (strstr(name, "_body")) encode_body(s, slot);
	} else if (strcmp(name, "color_preset") == 0 || strcmp(name, "scheme_preset") == 0) {
		bool color = strcmp(name, "color_preset") == 0;
		const char *previous = color ? ui->color_preset : ui->scheme_preset;
		const char *id = obs_data_get_string(s, name);
		if (strcmp(previous, id) != 0) {
			const struct nb_preset *pr = preset_by_id(id);
			if (pr) {
				bool is_color = pr >= nb_presets && pr < nb_presets + 6;
				if (color && is_color) apply_colors(s, pr);
				else if (!color && !is_color) apply_scheme(s, pr);
			}
			refresh = true;
		}
	} else if (ui && ui->ctx && strcmp(name, "ui_current") == 0) {
		control_select(ui->ctx, (int)obs_data_get_int(s, name));
	} else if (ui && ui->ctx && strcmp(name, "ui_paused") == 0) {
		control_pause_set(ui->ctx, obs_data_get_bool(s, name));
	}

	if (ui) {
		if (strcmp(name, "speed_x") == 0 || strcmp(name, "speed_y") == 0) {
			/* Initial apply_settings invokes callbacks too: only an actual
			 * speed edit should migrate a saved 1.3 overflow scroll. */
			const char *other = strcmp(name, "speed_x") == 0 ? "ui_last_x" : "ui_last_y";
			if (obs_data_get_double(s, name) != obs_data_get_double(s, other)) {
				obs_data_set_bool(s, "scroll_axis", true);
			}
		}
		bool scrolling = obs_data_get_bool(s, "scrolling");
		if (scrolling && !ui->last_scrolling) obs_data_set_bool(s, "scroll_axis", true);
		ui->last_scrolling = scrolling;
		obs_data_set_double(s, "ui_last_x", obs_data_get_double(s, "speed_x"));
		obs_data_set_double(s, "ui_last_y", obs_data_get_double(s, "speed_y"));
		struct dstr color_now = {0}, scheme_now = {0};
		color_signature(s, &color_now); scheme_signature(s, &scheme_now);
		if (strcmp(name, "color_preset") != 0 && dstr_cmp(&ui->color_sig, color_now.array) != 0) {
			obs_data_set_string(s, "color_preset", "custom");
		}
		if (strcmp(name, "scheme_preset") != 0 && dstr_cmp(&ui->scheme_sig, scheme_now.array) != 0) {
			obs_data_set_string(s, "scheme_preset", "custom");
		}
		dstr_free(&ui->color_sig); ui->color_sig = color_now;
		dstr_free(&ui->scheme_sig); ui->scheme_sig = scheme_now;
		snprintf(ui->color_preset, sizeof(ui->color_preset), "%s", obs_data_get_string(s, "color_preset"));
		snprintf(ui->scheme_preset, sizeof(ui->scheme_preset), "%s", obs_data_get_string(s, "scheme_preset"));
	}
	bool structural = strcmp(name, "ui_edit") == 0 || strcmp(name, "ui_multiline") == 0 ||
		strcmp(name, "scrolling") == 0 || strcmp(name, "show_advanced") == 0 ||
		strcmp(name, "mode") == 0 || strcmp(name, "outline") == 0 ||
		strcmp(name, "scroll_limit_width") == 0;
	if (ui && structural) {
		struct dstr now = {0}; structure_signature(s, &now);
		if (dstr_cmp(&ui->structure_sig, now.array) != 0) refresh = true;
		dstr_free(&now);
	}
	/* No per-keystroke list/status rebuild, property reload, source update or
	 * new timer. Native OBS draft persistence is independent of publishing live.
	 * Preset identities are correct immediately in settings, but visible labels
	 * refresh only on deliberate preset/structural/button actions or reopen. */
	if (refresh) ui_sync(props, s);
	if (ui) structure_signature(s, &ui->structure_sig);
	return refresh;
}

static obs_data_t *ui_settings(obs_properties_t *props)
{
	struct nb_ui *ui = obs_properties_get_param(props);
	return ui ? ui->settings : NULL;
}

static bool btn_apply(obs_properties_t *props, obs_property_t *p, void *data)
{
	struct nb_ctx *ctx = data;
	obs_data_t *s = ui_settings(props);
	if (s) obs_source_update(ctx->source, s);
	apply_to_live(ctx);
	ui_sync(props, s);
	return true;
}

static bool btn_revert(obs_properties_t *props, obs_property_t *p, void *data)
{
	struct nb_ctx *ctx = data;
	obs_data_t *s = ui_settings(props);
	obs_data_t *priv = obs_source_get_private_settings(ctx->source);
	obs_data_t *live = obs_data_get_obj(priv, "live");
	if (live && s) {
		set_defaults(live); copy_effective(s, live);
		ui_capture_presets(obs_properties_get_param(props), s);
		for (int i = 1; i <= NB_MAX_ITEMS; i++) encode_body(s, i);
		obs_source_update(ctx->source, s);
		ui_sync(props, s);
	}
	obs_data_release(live); obs_data_release(priv);
	return true;
}

static bool btn_replay(obs_properties_t *props, obs_property_t *p, void *data)
{
	control(data, NB_REPLAY);
	ui_sync(props, ui_settings(props));
	return true;
}

static bool btn_add(obs_properties_t *props, obs_property_t *p, void *data)
{
	struct nb_ctx *ctx = data;
	obs_data_t *s = ui_settings(props);
	int count = clampi(obs_data_get_int(s, "count"), 1, NB_MAX_ITEMS);
	if (count < NB_MAX_ITEMS) {
		item_clear(s, ++count); encode_body(s, count);
		obs_data_set_int(s, "count", count);
		obs_data_set_int(s, "ui_edit", count);
		obs_source_update(ctx->source, s);
	}
	ui_sync(props, s);
	return true;
}

static bool btn_delete(obs_properties_t *props, obs_property_t *p, void *data)
{
	struct nb_ctx *ctx = data;
	obs_data_t *s = ui_settings(props);
	int count = clampi(obs_data_get_int(s, "count"), 1, NB_MAX_ITEMS);
	int idx = clampi(obs_data_get_int(s, "ui_edit"), 1, count);
	if (count > 1) {
		for (int i = idx; i < count; i++) item_copy(s, i + 1, i);
		item_clear(s, count);
		obs_data_set_int(s, "count", --count);
		obs_data_set_int(s, "ui_edit", idx > count ? count : idx);
		for (int i = 1; i <= NB_MAX_ITEMS; i++) encode_body(s, i);
		obs_source_update(ctx->source, s);
	}
	ui_sync(props, s);
	return true;
}

static void attach_look_callback(obs_property_t *p)
{
	if (p)
		obs_property_set_modified_callback(p, ui_modified);
}

static void ui_attach(obs_properties_t *props)
{
	for (obs_property_t *p = obs_properties_first(props); p; obs_property_next(&p)) {
		if (obs_property_get_type(p) == OBS_PROPERTY_GROUP) ui_attach(obs_property_group_content(p));
		else if (obs_property_get_type(p) != OBS_PROPERTY_BUTTON &&
			 !(obs_property_get_type(p) == OBS_PROPERTY_TEXT && obs_property_text_type(p) == OBS_TEXT_INFO))
			obs_property_set_modified_callback(p, ui_modified);
	}
}

static obs_properties_t *nb_properties(void *data)
{
	struct nb_ctx *ctx = data;
	obs_properties_t *props = obs_properties_create();
	obs_property_t *p;

	struct nb_ui *ui = bzalloc(sizeof(*ui));
	ui->ctx = ctx;
	obs_properties_set_param(props, ui, ui_free);
	if (ctx) {
		obs_properties_add_text(props, "status", T("Status.DraftClean"), OBS_TEXT_INFO);
		p = obs_properties_add_list(props, "ui_current", T("Current"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
		obs_property_set_modified_callback(p, ui_modified);
		obs_properties_add_button2(props, "apply", T("Button.Apply"), btn_apply, ctx);
	}
	p = obs_properties_add_list(props, "ui_edit", T("EditItem"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_set_modified_callback(p, ui_modified);
	obs_properties_add_font(props, "body_font", T("BodyFont"));
	obs_properties_add_font(props, "title_font", T("TitleFont"));
	for (int i = 1; i <= NB_MAX_ITEMS; i++) {
		char k[32], name[64];
		obs_properties_t *g = obs_properties_create();
		snprintf(k, sizeof(k), "a%d_on", i);
		p = obs_properties_add_bool(g, k, T("Item.On"));
		obs_property_set_modified_callback(p, ui_modified);
		snprintf(k, sizeof(k), "a%d_title", i);
		p = obs_properties_add_text(g, k, T("Item.Title"), OBS_TEXT_DEFAULT);
		obs_property_set_modified_callback(p, ui_modified);
		snprintf(k, sizeof(k), "ui_body%d", i);
		p = obs_properties_add_text(g, k, T("Item.BodyCompact"), OBS_TEXT_DEFAULT);
		obs_property_set_long_description(p, T("Item.BodyCompact.Tip"));
		obs_property_set_modified_callback(p, ui_modified);
		snprintf(k, sizeof(k), "a%d_body", i);
		p = obs_properties_add_text(g, k, T("Item.Body"), OBS_TEXT_MULTILINE);
		obs_property_set_modified_callback(p, ui_modified);
		snprintf(k, sizeof(k), "grp_a%d", i);
		snprintf(name, sizeof(name), T("Item.Group"), i);
		obs_properties_add_group(props, k, name, OBS_GROUP_NORMAL, g);
	}
	p = obs_properties_add_bool(props, "ui_multiline", T("Multiline"));
	obs_property_set_modified_callback(p, ui_modified);
	if (ctx) {
		obs_properties_add_button2(props, "add_item", T("Button.Add"), btn_add, ctx);
		obs_properties_add_button2(props, "delete_item", T("Button.Delete"), btn_delete, ctx);
	}
	/* —— 外观 —— */
	obs_properties_t *look = obs_properties_create();
	p = obs_properties_add_list(look, "scheme_preset", T("SchemePreset"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(p, T("Preset.Custom"), "custom");
	for (size_t i = 6; i < sizeof(nb_presets)/sizeof(nb_presets[0]); i++)
		obs_property_list_add_string(p, T(nb_presets[i].locale), nb_presets[i].id);
	obs_property_set_long_description(p, T("SchemePreset.Tip"));
	p = obs_properties_add_list(look, "color_preset", T("ColorPreset"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(p, T("Preset.Custom"), "custom");
	for (size_t i = 0; i < 6; i++)
		obs_property_list_add_string(p, T(nb_presets[i].locale), nb_presets[i].id);
	obs_property_set_long_description(p, T("ColorPreset.Tip"));

	obs_properties_add_color(look, "text_color", T("TextColor"));
	obs_properties_add_color(look, "title_color", T("TitleColor"));
	p = obs_properties_add_int_slider(look, "text_opacity", T("TextOpacity"), 0, 100, 1);
	obs_property_int_set_suffix(p, "%");
	obs_properties_add_color(look, "bg_color", T("BgColor"));
	p = obs_properties_add_int_slider(look, "bg_opacity", T("BgOpacity"), 0, 100, 1);
	obs_property_int_set_suffix(p, "%");
	p = obs_properties_add_list(look, "align", T("Align"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(p, T("Align.Left"), "left");
	obs_property_list_add_string(p, T("Align.Center"), "center");
	obs_property_list_add_string(p, T("Align.Right"), "right");
	obs_properties_add_bool(look, "outline", T("Outline"));
	p = obs_properties_add_int_slider(look, "outline_size", T("OutlineSize"), 1, 20, 1);
	obs_property_int_set_suffix(p, " px");
	obs_properties_add_color(look, "outline_color", T("OutlineColor"));
	p = obs_properties_add_int_slider(look, "outline_opacity", T("OutlineOpacity"), 0, 100, 1);
	obs_property_int_set_suffix(p, "%");
	p = obs_properties_add_bool(look, "scrolling", T("Scrolling"));
	obs_property_set_long_description(p, T("Scrolling.Tip"));
	p = obs_properties_add_float_slider(look, "speed_x", T("SpeedX"), -500, 500, 1);
	obs_property_float_set_suffix(p, T("Unit.PxPerSec"));
	obs_property_set_long_description(p, T("SpeedX.Tip"));
	p = obs_properties_add_float_slider(look, "speed_y", T("SpeedY"), -500, 500, 1);
	obs_property_float_set_suffix(p, T("Unit.PxPerSec"));
	obs_property_set_long_description(p, T("SpeedY.Tip"));
	obs_properties_add_text(look, "legacy_scroll_info", T("LegacyScroll"), OBS_TEXT_INFO);
	p = obs_properties_add_bool(look, "show_advanced", T("ShowAdvanced"));
	obs_property_set_modified_callback(p, ui_modified);
	obs_properties_add_group(props, "grp_look", T("Group.Look"), OBS_GROUP_NORMAL, look);

	/* —— 高级 —— */
	obs_properties_t *adv = obs_properties_create();
	if (ctx) {
		obs_properties_add_button2(adv, "revert", T("Button.Revert"), btn_revert, ctx);
		p = obs_properties_add_bool(adv, "ui_paused", T("Pause"));
		obs_property_set_modified_callback(p, ui_modified);
		obs_properties_add_button2(adv, "replay", T("Button.Replay"), btn_replay, ctx);
	}
	p = obs_properties_add_list(adv, "mode", T("Mode"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(p, T("Mode.Fixed"), "fixed");
	obs_property_list_add_string(p, T("Mode.Rotate"), "rotate");
	obs_property_set_modified_callback(p, ui_modified);
	p = obs_properties_add_int(adv, "interval", T("Interval"), 2, 3600, 1);
	obs_property_int_set_suffix(p, T("Unit.Seconds"));
	p = obs_properties_add_int(adv, "scroll_height", T("ScrollHeight"), 60, 4096, 1);
	obs_property_int_set_suffix(p, " px");
	obs_properties_add_bool(adv, "scroll_limit_width", T("ScrollLimitWidth"));
	p = obs_properties_add_int(adv, "scroll_width", T("ScrollWidth"), 60, 4096, 1);
	obs_property_int_set_suffix(p, " px");
	attach_look_callback(obs_properties_add_color(adv, "border_color", T("BorderColor")));
	p = obs_properties_add_int_slider(adv, "border_opacity", T("BorderOpacity"), 0, 100, 1);
	obs_property_int_set_suffix(p, "%");
	attach_look_callback(p);
	p = obs_properties_add_int_slider(adv, "border_width", T("BorderWidth"), 0, 16, 1);
	obs_property_int_set_suffix(p, " px");
	attach_look_callback(p);
	p = obs_properties_add_int_slider(adv, "radius", T("Radius"), 0, 200, 1);
	obs_property_int_set_suffix(p, " px");
	attach_look_callback(p);
	p = obs_properties_add_int_slider(adv, "padding", T("Padding"), 0, 200, 1);
	obs_property_int_set_suffix(p, " px");
	attach_look_callback(p);
	p = obs_properties_add_int_slider(adv, "title_gap", T("TitleGap"), 0, 200, 1);
	obs_property_int_set_suffix(p, " px");
	attach_look_callback(p);
	obs_properties_add_float_slider(adv, "line_height", T("LineHeight"), 1.0, 3.0, 0.05);
	p = obs_properties_add_int_slider(adv, "speed", T("Speed"), 5, 400, 1);
	obs_property_int_set_suffix(p, T("Unit.PxPerSec"));
	p = obs_properties_add_float_slider(adv, "top_dwell", T("TopDwell"), 0, 60, 0.5);
	obs_property_float_set_suffix(p, T("Unit.Seconds"));
	p = obs_properties_add_float_slider(adv, "bottom_dwell", T("BottomDwell"), 0, 60, 0.5);
	obs_property_float_set_suffix(p, T("Unit.Seconds"));
	p = obs_properties_add_float_slider(adv, "fade", T("Fade"), 0, 5, 0.05);
	obs_property_float_set_suffix(p, T("Unit.Seconds"));
	obs_properties_add_group(props, "grp_adv", T("Group.Advanced"), OBS_GROUP_NORMAL, adv);

	ui_attach(props);
	/* Capture initial values; apply_settings will rebind to OBS's view copy. */
	if (ctx) {
		obs_data_t *ss = obs_source_get_settings(ctx->source);
		ui_bind(props, ss);
		ui_sync(props, ss);
		obs_data_release(ss);
	}
	return props;
}

struct obs_source_info nb_source_info = {
	.id = "obs_noticeboard",
	.type = OBS_SOURCE_TYPE_INPUT,
	.output_flags = OBS_SOURCE_VIDEO | OBS_SOURCE_CUSTOM_DRAW,
	.get_name = nb_get_name,
	.create = nb_create,
	.destroy = nb_destroy,
	.update = nb_update,
	.load = nb_load,
	.save = nb_save,
	.get_defaults = nb_get_defaults,
	.get_properties = nb_properties,
	.get_width = nb_get_width,
	.get_height = nb_get_height,
	.video_tick = nb_tick,
	.video_render = nb_render,
	.icon_type = OBS_ICON_TYPE_TEXT,
};
