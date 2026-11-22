/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
/* 真实 libobs (OBS 32.2.2 的 obs.dll + libobs-d3d11) 加载测试：
 * 加载插件 → 创建「公告板」来源 → 用 D3D11 离屏渲染 → 保存截图，并检查草稿 / 应用逻辑。
 * 运行时工作目录必须是 OBS 的 bin\64bit（libobs 从 ../../data/libobs 找着色器）。 */
#include <obs.h>
#include <obs-module.h>
#include <windows.h>
#include <stdio.h>
#include <string.h>

static FILE *g_log;
static void log_handler(int lvl, const char *fmt, va_list args, void *p)
{
	char buf[4096];
	vsnprintf(buf, sizeof(buf), fmt, args);
	fprintf(g_log, "[%d] %s\n", lvl, buf);
	fflush(g_log);
}

static LONG WINAPI on_exception(EXCEPTION_POINTERS *ep)
{
 HMODULE mod=NULL;char path[MAX_PATH]={0};
 GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(const char*)ep->ExceptionRecord->ExceptionAddress,&mod);
 GetModuleFileNameA(mod,path,sizeof(path));
 fprintf(g_log,"EXCEPTION %08lx at %p module=%s rva=%llx addr=%llx operation=%llu\n",ep->ExceptionRecord->ExceptionCode,ep->ExceptionRecord->ExceptionAddress,path,(unsigned long long)((uintptr_t)ep->ExceptionRecord->ExceptionAddress-(uintptr_t)mod),(unsigned long long)ep->ExceptionRecord->ExceptionInformation[1],(unsigned long long)ep->ExceptionRecord->ExceptionInformation[0]);fflush(g_log);
 return EXCEPTION_EXECUTE_HANDLER;
}

static int failures = 0;
#define CHECK(cond, msg)                                              \
	do {                                                          \
		if (cond)                                             \
			fprintf(g_log, "PASS: %s\n", msg);             \
		else {                                                \
			fprintf(g_log, "FAIL: %s\n", msg);             \
			failures++;                                   \
		}                                                     \
		fflush(g_log);                                         \
	} while (0)

static const char *outdir;

/* 渲染来源到 RGBA 并保存为 32 位 BMP；返回非透明像素数 */
static long snap(obs_source_t *src, const char *name, unsigned *hash)
{
	uint32_t w = obs_source_get_width(src), h = obs_source_get_height(src);
	long opaque = 0;
	unsigned hs = 2166136261u;
	obs_enter_graphics();
	gs_texrender_t *tr = gs_texrender_create(GS_RGBA, GS_ZS_NONE);
	gs_stagesurf_t *st = gs_stagesurface_create(w, h, GS_RGBA);
	if (gs_texrender_begin(tr, w, h)) {
		struct vec4 zero;
		vec4_zero(&zero);
		gs_clear(GS_CLEAR_COLOR, &zero, 0.0f, 0);
		gs_ortho(0.0f, (float)w, 0.0f, (float)h, -100.0f, 100.0f);
		gs_blend_state_push();
		gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
		obs_source_video_render(src);
		gs_blend_state_pop();
		gs_texrender_end(tr);
	}
	gs_stage_texture(st, gs_texrender_get_texture(tr));
	uint8_t *data;
	uint32_t ls;
	if (gs_stagesurface_map(st, &data, &ls)) {
		char path[MAX_PATH];
		snprintf(path, sizeof(path), "%s\\%s.bmp", outdir, name);
		FILE *f = fopen(path, "wb");
		BITMAPFILEHEADER fh = {0};
		BITMAPV4HEADER ih = {0};
		ih.bV4Size = sizeof(ih);
		ih.bV4Width = (LONG)w;
		ih.bV4Height = -(LONG)h;
		ih.bV4Planes = 1;
		ih.bV4BitCount = 32;
		ih.bV4V4Compression = BI_BITFIELDS;
		ih.bV4RedMask = 0x00FF0000;
		ih.bV4GreenMask = 0x0000FF00;
		ih.bV4BlueMask = 0x000000FF;
		ih.bV4AlphaMask = 0xFF000000;
		fh.bfType = 0x4D42;
		fh.bfOffBits = sizeof(fh) + sizeof(ih);
		fh.bfSize = fh.bfOffBits + w * h * 4;
		fwrite(&fh, sizeof(fh), 1, f);
		fwrite(&ih, sizeof(ih), 1, f);
		for (uint32_t y = 0; y < h; y++) {
			uint8_t *row = data + y * ls;
			for (uint32_t x = 0; x < w; x++) {
				uint8_t px[4] = {row[x * 4 + 2], row[x * 4 + 1], row[x * 4 + 0], row[x * 4 + 3]};
				if (px[3] > 8)
					opaque++;
				for (int k = 0; k < 4; k++)
					hs = (hs ^ px[k]) * 16777619u;
				fwrite(px, 4, 1, f);
			}
		}
		fclose(f);
		gs_stagesurface_unmap(st);
	}
	gs_stagesurface_destroy(st);
	gs_texrender_destroy(tr);
	obs_leave_graphics();
	if (hash)
		*hash = hs;
	fprintf(g_log, "snap %s %ux%u opaque=%ld hash=%08x\n", name, w, h, opaque, hs);
	fflush(g_log);
	return opaque;
}


static obs_data_t *live_settings(obs_source_t *src)
{
 obs_data_t *p=obs_source_get_private_settings(src), *l=obs_data_get_obj(p,"live");
 obs_data_release(p); return l;
}
static void modify(obs_properties_t *p, obs_data_t *s, const char *name)
{
 obs_property_t *prop=obs_properties_get(p,name);
 CHECK(prop!=NULL,name); if(prop) obs_property_modified(prop,s);
}
static void button(obs_properties_t *p, obs_source_t *src, const char *name)
{
 obs_property_t *prop=obs_properties_get(p,name);
 CHECK(prop!=NULL,name); if(prop) obs_property_button_clicked(prop,src);
}
static int visible_editors(obs_properties_t *p)
{
 int n=0; for(int i=1;i<=20;i++) {char k[32];snprintf(k,sizeof(k),"grp_a%d",i);
 if(obs_property_visible(obs_properties_get(p,k))) n++;} return n;
}
static void apply(obs_properties_t *p, obs_source_t *src)
{
 button(p,src,"apply"); Sleep(150);
}
static void select_live(obs_properties_t *p, obs_data_t *s, int slot)
{
 obs_data_set_int(s,"ui_current",slot);modify(p,s,"ui_current");
}

struct pixels {int w,h; unsigned char *data;};
static struct pixels pixels_read(const char *name)
{
 char path[MAX_PATH];snprintf(path,sizeof(path),"%s\\%s.bmp",outdir,name);
 FILE *f=fopen(path,"rb");struct pixels p={0};if(!f)return p;
 BITMAPFILEHEADER bh;BITMAPINFOHEADER ih;fread(&bh,sizeof(bh),1,f);fread(&ih,sizeof(ih),1,f);
 p.w=ih.biWidth;p.h=ih.biHeight<0?-ih.biHeight:ih.biHeight;
 p.data=malloc((size_t)p.w*p.h*4);fseek(f,bh.bfOffBits,SEEK_SET);fread(p.data,(size_t)p.w*p.h*4,1,f);fclose(f);return p;
}
static void measured_shift(const char *a,const char *b,int *sx,int *sy)
{
 struct pixels x=pixels_read(a),y=pixels_read(b);double best=1e30;*sx=*sy=0;
 if(!x.data||!y.data||x.w!=y.w||x.h!=y.h)goto done;
 for(int dy=-20;dy<=20;dy++)for(int dx=-64;dx<=64;dx++) {
  double score=0;
  for(int j=0;j<x.h;j++)for(int i=0;i<x.w;i++) {
   int jj=(j+dy+x.h)%x.h,ii=(i+dx+x.w)%x.w;
   size_t p=((size_t)j*x.w+i)*4,q=((size_t)jj*x.w+ii)*4;
   int d=(int)y.data[p+3]-(int)x.data[q+3];score+=(double)d*d;
  }
  if(score<best){best=score;*sx=dx;*sy=dy;}
 }
 fprintf(g_log,"measured sampling shift %s => %s: (%d,%d), score=%.0f\n",a,b,*sx,*sy,best);
 done:free(x.data);free(y.data);
}
static int red_pixels(const char *name,int y0,int y1)
{
 struct pixels p=pixels_read(name);int n=0;if(!p.data)return 0;if(y1>p.h)y1=p.h;
 for(int y=y0;y<y1;y++)for(int x=0;x<p.w;x++){unsigned char *c=p.data+((size_t)y*p.w+x)*4;if(c[2]>80&&c[2]>c[1]+40&&c[2]>c[0]+40&&c[3]>20)n++;}
 free(p.data);return n;
}
static int max_alpha(const char *name)
{
 struct pixels p=pixels_read(name);int m=0;
 if(p.data)for(size_t i=0,n=(size_t)p.w*p.h;i<n;i++)if(p.data[i*4+3]>m)m=p.data[i*4+3];
 free(p.data);return m;
}
static bool clear_edges(const char *name)
{
 struct pixels p=pixels_read(name);bool clear=p.data!=NULL;
 if(p.data)for(int y=0;y<p.h;y++)for(int x=0;x<p.w;x++)if(x<2||y<2||x>=p.w-2||y>=p.h-2)if(p.data[((size_t)y*p.w+x)*4+3]>4)clear=false;
 free(p.data);return clear;
}
static void axis_case(obs_source_t *src,obs_properties_t *p,obs_data_t *s,double vx,double vy,const char *name)
{
 obs_data_set_bool(s,"scrolling",true);modify(p,s,"scrolling");
 obs_data_set_double(s,"speed_x",vx);modify(p,s,"speed_x");
 obs_data_set_double(s,"speed_y",vy);modify(p,s,"speed_y");
 obs_data_set_bool(s,"scroll_axis",true);apply(p,src);
 char a[64],b[64];snprintf(a,sizeof(a),"axis-%s-a",name);snprintf(b,sizeof(b),"axis-%s-b",name);
 unsigned h1,h2;snap(src,a,&h1);Sleep(180);snap(src,b,&h2);
 if(vx==0&&vy==0)CHECK(h1==h2,"axis zero/zero visually stationary");
 else {
  int dx,dy;measured_shift(a,b,&dx,&dy);
  CHECK(vx>0?dx>0:vx<0?dx<0:dx==0,"horizontal sign and zero match OBS sampling direction");
  CHECK(vy>0?dy>0:vy<0?dy<0:dy==0,"vertical sign and zero match OBS sampling direction");
  CHECK(h1!=h2,"axis scrolling moves short text without overflow");
 }
}

static unsigned hash_text(unsigned h,const char *s)
{
 for(;*s;s++) h=(h^(unsigned char)*s)*16777619u;
 return h;
}
static unsigned colors_hash(obs_data_t *s)
{
 const char *keys[]={"title_color","text_color","bg_color","border_color","outline_color"};unsigned h=2166136261u;
 for(int i=0;i<5;i++)h=(h^((unsigned)obs_data_get_int(s,keys[i])&0xFFFFFFu))*16777619u;
 return h;
}
static unsigned scheme_hash(obs_data_t *s)
{
 const char *keys[]={"text_opacity","bg_opacity","outline_size","outline_opacity","border_opacity","border_width","radius","padding","title_gap"};
 unsigned h=2166136261u;char buf[256];
 for(int i=0;i<9;i++){snprintf(buf,sizeof(buf),"%lld;",obs_data_get_int(s,keys[i]));h=hash_text(h,buf);}
 h=(h^(unsigned)obs_data_get_bool(s,"outline"))*16777619u;
 snprintf(buf,sizeof(buf),"%.5f;%s;",obs_data_get_double(s,"line_height"),obs_data_get_string(s,"align"));h=hash_text(h,buf);
 const char *fonts[]={"title_font","body_font"};
 for(int i=0;i<2;i++){obs_data_t *f=obs_data_get_obj(s,fonts[i]);h=hash_text(h,obs_data_get_string(f,"face"));snprintf(buf,sizeof(buf),"%lld;%lld;",obs_data_get_int(f,"size"),obs_data_get_int(f,"flags"));h=hash_text(h,buf);obs_data_release(f);}
 return h;
}
static void choose(obs_properties_t *p,obs_data_t *s,const char *key,const char *id)
{
 obs_data_set_string(s,key,id);modify(p,s,key);
}

static void count_property_refresh(void *data,calldata_t *params)
{
 (void)params;(*(unsigned *)data)++;
}
static void continuous_callback(obs_source_t *src,obs_properties_t *p,obs_data_t *s,const char *key,unsigned *signals)
{
 /* Match OBSBasicProperties visUpdateCb ordering: persist draft first, then
  * invoke modified. This must still not emit a source property reload. */
 obs_source_update(src,s);
 obs_property_t *prop=obs_properties_get(p,key);
 CHECK(prop!=NULL,key);
 if(prop) CHECK(!obs_property_modified(prop,s),"continuous edit returns FALSE: OBS must not queue full RefreshProperties");
 CHECK(*signals==0,"continuous edit emits no source update_properties structural signal");
}
static void test_142(void)
{
 obs_source_t *src=obs_source_create("obs_noticeboard","nb142-stable-edit",NULL,NULL);
 obs_properties_t *p=obs_source_properties(src);obs_data_t *s=obs_source_get_settings(src);obs_properties_apply_settings(p,s);
 choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","warm");
 obs_data_set_double(s,"fade",0);modify(p,s,"fade");apply(p,src);
 obs_data_t *l=live_settings(src);char original[512];snprintf(original,sizeof(original),"%s",obs_data_get_string(l,"a1_body"));unsigned live_colors=colors_hash(l),live_scheme=scheme_hash(l);obs_data_release(l);
 unsigned signals=0;signal_handler_t *handler=obs_source_get_signal_handler(src);signal_handler_connect(handler,"update_properties",count_property_refresh,&signals);
 const char *intkeys[]={"text_opacity","bg_opacity","border_opacity","outline_opacity","outline_size","border_width","radius","padding","title_gap","scroll_height","scroll_width","speed","interval"};
 const int starts[]={75,45,25,75,2,0,3,10,6,200,450,45,30};
 for(int k=0;k<13;k++) {
  choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","warm");
  char status[256];snprintf(status,sizeof(status),"%s",obs_property_description(obs_properties_get(p,"status")));
  for(int step=1;step<=16;step++) {
   obs_data_set_int(s,intkeys[k],starts[k]+step);continuous_callback(src,p,s,intkeys[k],&signals);
   CHECK(strcmp(obs_property_description(obs_properties_get(p,"status")),status)==0,"ordinary edits do not mutate/rebuild status metadata on each value");
   CHECK(strcmp(obs_data_get_string(s,"color_preset"),"warm")==0,"continuous non-color edits preserve named palette");
   if(k<9)CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0,"FIRST and repeated named-scheme manual edits immediately store Custom without UI rebuild");
  }
 }
 const char *rgbkeys[]={"title_color","text_color","bg_color","border_color","outline_color"};
 for(int k=0;k<5;k++) {
  choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","warm");
  for(int step=1;step<=16;step++) {
   obs_data_set_int(s,rgbkeys[k],0xFF567800+step);continuous_callback(src,p,s,rgbkeys[k],&signals);
   CHECK(strcmp(obs_data_get_string(s,"color_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"scheme_preset"),"game")==0,"FIRST and repeated RGB edits store only color Custom without UI rebuild");
  }
 }
 const char *doublekeys[]={"speed_x","speed_y","line_height","fade","top_dwell","bottom_dwell"};
 for(int k=0;k<6;k++) {
  choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","warm");
  for(int step=1;step<=16;step++) {
   double value=k<2?-8.0+step:(k==2?1.0+step*.05:(k==3?step*.05:step*.5));
   obs_data_set_double(s,doublekeys[k],value);continuous_callback(src,p,s,doublekeys[k],&signals);
  }
 }
 const char *fonts[]={"body_font","title_font"};
 for(int k=0;k<2;k++) {
  choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","warm");
  for(int step=1;step<=16;step++) {
   obs_data_t *f=obs_data_get_obj(s,fonts[k]),*copy=obs_data_create();obs_data_apply(copy,f);obs_data_set_int(copy,"size",30+step);obs_data_set_obj(s,fonts[k],copy);obs_data_release(copy);obs_data_release(f);
   continuous_callback(src,p,s,fonts[k],&signals);
   CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"color_preset"),"warm")==0,"font size first/repeated edits preserve palette and do not rebuild");
  }
 }
 const char *textkeys[]={"a1_title","ui_body1","a1_body"};const char *texts[]={"Continuous title typing 123456789","compact\\nbody\\\\literal","multiline\nbody\ncontinued"};
 for(int k=0;k<3;k++) {
  char label[512];snprintf(label,sizeof(label),"%s",obs_property_list_item_name(obs_properties_get(p,"ui_edit"),0));
  for(size_t step=1;step<=strlen(texts[k]);step++) {
   char value[128];memcpy(value,texts[k],step);value[step]=0;obs_data_set_string(s,textkeys[k],value);continuous_callback(src,p,s,textkeys[k],&signals);
   CHECK(strcmp(label,obs_property_list_item_name(obs_properties_get(p,"ui_edit"),0))==0,"typing never clears/repopulates announcement selector on each character");
  }
  CHECK(strcmp(obs_data_get_string(s,textkeys[k]),texts[k])==0,"multi-character title/body input is preserved exactly");
 }
 const char *cn[]={"公","公告","公告正","公告正文","公告正文\\n","公告正文\\n第","公告正文\\n第二行"};
 for(int i=0;i<7;i++){obs_data_set_string(s,"ui_body1",cn[i]);continuous_callback(src,p,s,"ui_body1",&signals);}
 CHECK(strcmp(obs_data_get_string(s,"a1_body"),"公告正文\n第二行")==0,"progressive UTF8 compact editing preserves complete Chinese text");
 l=live_settings(src);CHECK(colors_hash(l)==live_colors&&scheme_hash(l)==live_scheme&&strcmp(obs_data_get_string(l,"a1_body"),original)==0,"all continuous edits leave applied live style/body untouched");obs_data_release(l);
 obs_source_update(src,s);Sleep(200);
 CHECK(signals==0,"native visual draft-save source update emits no property reload");
 l=live_settings(src);CHECK(colors_hash(l)==live_colors&&scheme_hash(l)==live_scheme&&strcmp(obs_data_get_string(l,"a1_body"),original)==0,"native draft persistence is NOT automatic publishing");obs_data_release(l);
 /* Only real dependent-control changes rebuild; repeated same value does not. */
 obs_data_set_bool(s,"outline",false);CHECK(obs_property_modified(obs_properties_get(p,"outline"),s),"outline toggle refreshes dependent controls");CHECK(!obs_property_visible(obs_properties_get(p,"outline_size")),"outline children hidden on deliberate toggle");
 CHECK(!obs_property_modified(obs_properties_get(p,"outline"),s),"unchanged structural checkbox returns false");
 obs_data_set_bool(s,"outline",true);CHECK(obs_property_modified(obs_properties_get(p,"outline"),s),"outline ON refreshes child visibility");
 obs_data_set_bool(s,"show_advanced",true);CHECK(obs_property_modified(obs_properties_get(p,"show_advanced"),s),"opening advanced intentionally rebuilds once");
 CHECK(!obs_property_modified(obs_properties_get(p,"show_advanced"),s),"unchanged advanced control does not rebuild again");
 obs_data_set_bool(s,"scrolling",true);CHECK(obs_property_modified(obs_properties_get(p,"scrolling"),s),"scroll ON reveals axis controls");
 for(int step=0;step<16;step++){obs_data_set_double(s,"speed_x",step-8);continuous_callback(src,p,s,"speed_x",&signals);obs_data_set_double(s,"speed_y",8-step);continuous_callback(src,p,s,"speed_y",&signals);}
 obs_data_set_bool(s,"scroll_axis",false);obs_data_set_int(s,"speed",51);modify(p,s,"show_advanced");
 obs_data_set_double(s,"speed_x",123);continuous_callback(src,p,s,"speed_x",&signals);CHECK(obs_data_get_bool(s,"scroll_axis")&&obs_data_get_int(s,"speed")==51,"first legacy-axis opt-in does not rebuild or reinterpret old speed");
 obs_data_set_int(s,"ui_edit",1);CHECK(!obs_property_modified(obs_properties_get(p,"ui_edit"),s),"unchanged editor selector is stable");
 obs_data_set_bool(s,"a1_on",false);continuous_callback(src,p,s,"a1_on",&signals);obs_data_set_bool(s,"a1_on",true);continuous_callback(src,p,s,"a1_on",&signals);
 obs_data_set_bool(s,"ui_paused",true);continuous_callback(src,p,s,"ui_paused",&signals);obs_data_set_bool(s,"ui_paused",false);continuous_callback(src,p,s,"ui_paused",&signals);
 obs_data_set_int(s,"ui_current",1);continuous_callback(src,p,s,"ui_current",&signals);
 obs_data_set_bool(s,"scrolling",false);modify(p,s,"scrolling");apply(p,src);
 l=live_settings(src);CHECK(strcmp(obs_data_get_string(l,"a1_body"),obs_data_get_string(s,"a1_body"))==0&&strcmp(obs_data_get_string(l,"a1_title"),texts[0])==0,"explicit Apply still publishes last typed input after long continuous edit");obs_data_release(l);
 CHECK(signals==0,"entire continuous edit/apply flow emits no update_properties signal");signal_handler_disconnect(handler,"update_properties",count_property_refresh,&signals);
 obs_properties_destroy(p);obs_data_release(s);obs_source_release(src);
}
static void test_141(void)
{
 obs_source_t *src=obs_source_create("obs_noticeboard","nb141-preset-cross",NULL,NULL);
 obs_properties_t *p=obs_source_properties(src);obs_data_t *s=obs_source_get_settings(src);obs_properties_apply_settings(p,s);
 CHECK(obs_properties_get(p,"color_preset")&&obs_properties_get(p,"scheme_preset")&&!obs_properties_get(p,"preset"),"real property keys color_preset and scheme_preset; no combined preset widget");
 CHECK(obs_property_list_item_count(obs_properties_get(p,"color_preset"))==7&&obs_property_list_item_count(obs_properties_get(p,"scheme_preset"))==7,"exactly six selectable colors and six selectable schemes plus each Custom");
 const char *palettes[]={"warm","ivory","sakura","ink","glass","violet"};
 const char *schemes[]={"game","chat","caption","alert","quiet","note"};
 obs_data_set_string(s,"ui_body1","独立颜色与方案\n保留正文和字号");modify(p,s,"ui_body1");
 obs_data_set_double(s,"fade",0);modify(p,s,"fade");apply(p,src);
 const int sizes[]={24,26,36,40,22,30},opacity[]={28,72,58,96,0,95};
 for(int i=0;i<6;i++) {
  unsigned before_colors=colors_hash(s);choose(p,s,"scheme_preset",schemes[i]);
  CHECK(colors_hash(s)==before_colors,"scheme selection preserves all five RGB colors");
  for(int j=0;j<6;j++) {
   unsigned before=scheme_hash(s);choose(p,s,"color_preset",palettes[j]);
   CHECK(scheme_hash(s)==before,"all 36 scheme/color crosses preserve fonts/size/opacity/outline/layout");
   CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),schemes[i])==0&&strcmp(obs_data_get_string(s,"color_preset"),palettes[j])==0,"both independent dropdown identities remain selected after cross");
   obs_data_t *font=obs_data_get_obj(s,"body_font");CHECK(obs_data_get_int(font,"size")==sizes[i]&&obs_data_get_int(s,"bg_opacity")==opacity[i],"color swap never resets scene typography or background opacity");obs_data_release(font);
   unsigned palette=colors_hash(s);choose(p,s,"scheme_preset",schemes[(i+1)%6]);
   CHECK(colors_hash(s)==palette&&strcmp(obs_data_get_string(s,"color_preset"),palettes[j])==0,"reverse order: scheme swap preserves chosen palette and its identity");
   choose(p,s,"scheme_preset",schemes[i]);
   CHECK(colors_hash(s)==palette,"restoring scheme still does not replace colors");
   CHECK(strcmp(obs_data_get_string(s,"a1_body"),"独立颜色与方案\n保留正文和字号")==0,"cross presets do not lose multiline body");
   apply(p,src);
   obs_data_t *l=live_settings(src);CHECK(strcmp(obs_data_get_string(l,"color_preset"),palettes[j])==0&&strcmp(obs_data_get_string(l,"scheme_preset"),schemes[i])==0,"applied snapshot persists both preset IDs");obs_data_release(l);
   char file[80];snprintf(file,sizeof(file),"cross-%s-%s",schemes[i],palettes[j]);CHECK(snap(src,file,NULL)>100,"every cross renders actual pixels");
  }
 }
 choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","ivory");apply(p,src);
 unsigned palette=colors_hash(s);obs_data_set_int(s,"outline_size",7);modify(p,s,"outline_size");
 CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"color_preset"),"ivory")==0&&colors_hash(s)==palette,"manual outline size invalidates ONLY scheme");
 choose(p,s,"scheme_preset","chat");obs_data_set_int(s,"text_color",0xFF123456);modify(p,s,"text_color");
 CHECK(strcmp(obs_data_get_string(s,"color_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"scheme_preset"),"chat")==0,"manual RGB edit invalidates ONLY color preset");
 choose(p,s,"color_preset","warm");palette=colors_hash(s);obs_data_set_int(s,"bg_opacity",37);modify(p,s,"bg_opacity");
 CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"color_preset"),"warm")==0&&colors_hash(s)==palette,"opacity is NOT in color signature; only scheme becomes Custom");
 choose(p,s,"scheme_preset","caption");obs_data_t *font=obs_data_get_obj(s,"body_font"),*copy=obs_data_create();obs_data_apply(copy,font);obs_data_set_int(copy,"size",41);obs_data_set_obj(s,"body_font",copy);obs_data_release(copy);obs_data_release(font);modify(p,s,"body_font");
 CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"color_preset"),"warm")==0,"manual font invalidates ONLY scheme");
 /* Verify every RGB field belongs ONLY to the color signature. */
 const char *rgb_keys[]={"title_color","text_color","bg_color","border_color","outline_color"};
 for(int k=0;k<5;k++) {
  choose(p,s,"scheme_preset","caption");choose(p,s,"color_preset","warm");unsigned shape=scheme_hash(s);
  obs_data_set_int(s,rgb_keys[k],obs_data_get_int(s,rgb_keys[k])^1);modify(p,s,rgb_keys[k]);
  CHECK(strcmp(obs_data_get_string(s,"color_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"scheme_preset"),"caption")==0&&scheme_hash(s)==shape,"each of five manual RGB edits invalidates ONLY colors");
 }
 const char *opacity_keys[]={"text_opacity","bg_opacity","border_opacity","outline_opacity"};
 for(int k=0;k<4;k++) {
  choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","ivory");unsigned colors=colors_hash(s);
  obs_data_set_int(s,opacity_keys[k],obs_data_get_int(s,opacity_keys[k])-1);modify(p,s,opacity_keys[k]);
  CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"color_preset"),"ivory")==0&&colors_hash(s)==colors,"each of four opacity edits invalidates ONLY scheme");
 }
 choose(p,s,"scheme_preset","game");obs_data_set_bool(s,"outline",false);modify(p,s,"outline");
 CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(s,"color_preset"),"ivory")==0,"outline checkbox invalidates ONLY scheme");
 choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","ivory");apply(p,src);
 unsigned sh=scheme_hash(s),ch=colors_hash(s);
 obs_properties_t *rp=obs_source_properties(src);obs_properties_apply_settings(rp,s);
 CHECK(scheme_hash(s)==sh&&colors_hash(s)==ch&&strcmp(obs_data_get_string(s,"scheme_preset"),"game")==0&&strcmp(obs_data_get_string(s,"color_preset"),"ivory")==0,"property reconstruction keeps both choices and actual style");obs_properties_destroy(rp);
 choose(p,s,"color_preset","sakura");obs_source_update(src,s);
 obs_data_t *l=live_settings(src);CHECK(strcmp(obs_data_get_string(l,"color_preset"),"ivory")==0,"new palette remains draft until Apply");obs_data_release(l);
 obs_data_t *saved=obs_save_source(src),*json=obs_data_create_from_json(obs_data_get_json(saved));obs_data_release(saved);obs_data_erase(json,"uuid");obs_data_set_string(json,"name","nb141-pair-reloaded");obs_source_t *re=obs_load_source(json);obs_source_load2(re);obs_data_release(json);
 obs_data_t *rs=obs_source_get_settings(re);obs_properties_t *rprops=obs_source_properties(re);obs_properties_apply_settings(rprops,rs);l=live_settings(re);
 CHECK(strcmp(obs_data_get_string(rs,"color_preset"),"sakura")==0&&strcmp(obs_data_get_string(rs,"scheme_preset"),"game")==0,"save/reload retains draft pair independently");
 CHECK(strcmp(obs_data_get_string(l,"color_preset"),"ivory")==0&&strcmp(obs_data_get_string(l,"scheme_preset"),"game")==0,"save/reload retains different live pair independently");obs_data_release(l);obs_properties_destroy(rprops);obs_data_release(rs);obs_source_release(re);
 button(p,src,"revert");modify(p,s,"ui_multiline");
 CHECK(strcmp(obs_data_get_string(s,"color_preset"),"ivory")==0&&strcmp(obs_data_get_string(s,"scheme_preset"),"game")==0,"Revert restores both IDs and updates independent signature caches");
 /* Legacy 1.4 scene style + an independent palette, with NO new ID fields. */
 choose(p,s,"scheme_preset","note");choose(p,s,"color_preset","ink");
 obs_data_t *legacy=obs_data_create_from_json(obs_data_get_json_with_defaults(s));obs_data_erase(legacy,"color_preset");obs_data_erase(legacy,"scheme_preset");obs_data_set_string(legacy,"preset","custom");
 sh=scheme_hash(legacy);ch=colors_hash(legacy);
 obs_source_t *old=obs_source_create("obs_noticeboard","nb141-legacy14",legacy,NULL);obs_data_release(legacy);rs=obs_source_get_settings(old);rprops=obs_source_properties(old);obs_properties_apply_settings(rprops,rs);
 CHECK(sh==scheme_hash(rs)&&ch==colors_hash(rs),"migrating just-installed 1.4 NEVER reapplies or resets actual style");
 CHECK(strcmp(obs_data_get_string(rs,"scheme_preset"),"note")==0&&strcmp(obs_data_get_string(rs,"color_preset"),"ink")==0,"legacy actual values infer independent scene and color labels");
 CHECK(strcmp(obs_data_get_string(rs,"preset"),"custom")==0,"legacy preset metadata is retained unchanged");
 obs_properties_destroy(rprops);obs_data_release(rs);obs_source_release(old);
 legacy=obs_data_create();obs_data_set_string(legacy,"preset","warm");obs_data_set_int(legacy,"bg_opacity",37);obs_data_set_int(legacy,"padding",19);obs_data_set_int(legacy,"text_color",0xFF123456);obs_data_set_string(legacy,"a1_body","1.3 原文\n不能重置");
 old=obs_source_create("obs_noticeboard","nb141-legacy13",legacy,NULL);obs_data_release(legacy);rs=obs_source_get_settings(old);sh=scheme_hash(rs);ch=colors_hash(rs);rprops=obs_source_properties(old);obs_properties_apply_settings(rprops,rs);
 CHECK(sh==scheme_hash(rs)&&ch==colors_hash(rs)&&strcmp(obs_data_get_string(rs,"a1_body"),"1.3 原文\n不能重置")==0,"1.3 custom style/content survives old preset=warm without any reset");
 CHECK(strcmp(obs_data_get_string(rs,"scheme_preset"),"custom")==0&&strcmp(obs_data_get_string(rs,"color_preset"),"custom")==0,"unmatched legacy style truthfully shows each independent Custom");obs_properties_destroy(rprops);obs_data_release(rs);obs_source_release(old);
 choose(p,s,"scheme_preset","game");choose(p,s,"color_preset","ivory");
 obs_data_set_int(s,"padding",13);modify(p,s,"padding");obs_data_set_int(s,"text_color",0xFF456789);modify(p,s,"text_color");apply(p,src);sh=scheme_hash(s);ch=colors_hash(s);
 saved=obs_save_source(src);json=obs_data_create_from_json(obs_data_get_json(saved));obs_data_release(saved);obs_data_erase(json,"uuid");obs_data_set_string(json,"name","nb141-both-custom");re=obs_load_source(json);obs_source_load2(re);obs_data_release(json);rs=obs_source_get_settings(re);rprops=obs_source_properties(re);obs_properties_apply_settings(rprops,rs);
 CHECK(strcmp(obs_data_get_string(rs,"color_preset"),"custom")==0&&strcmp(obs_data_get_string(rs,"scheme_preset"),"custom")==0,"both independent Custom selections survive JSON save/reload");
 CHECK(scheme_hash(rs)==sh&&colors_hash(rs)==ch,"custom roundtrip does not infer or reapply stale legacy preset");obs_properties_destroy(rprops);obs_data_release(rs);obs_source_release(re);
 obs_properties_destroy(p);obs_data_release(s);obs_source_release(src);
}
static void test_140(void)
{
 obs_data_t *init=obs_data_create();obs_data_set_string(init,"a1_title","标题");obs_data_set_string(init,"a1_body","Outline 汉字 Ag");
 obs_data_set_int(init,"padding",0);obs_data_set_int(init,"border_width",0);obs_data_set_int(init,"bg_opacity",0);obs_data_set_int(init,"radius",0);obs_data_set_double(init,"fade",0);
 obs_source_t *src=obs_source_create("obs_noticeboard","nb-140-features",init,NULL);obs_data_release(init);
 obs_properties_t *p=obs_source_properties(src);obs_data_t *s=obs_source_get_settings(src);obs_properties_apply_settings(p,s);
 CHECK(obs_property_list_item_count(obs_properties_get(p,"color_preset"))==7&&obs_property_list_item_count(obs_properties_get(p,"scheme_preset"))==7,"TWO independent preset properties: six colors + six schemes, each plus Custom");
 CHECK(obs_property_float_min(obs_properties_get(p,"speed_x"))==-500&&obs_property_float_max(obs_properties_get(p,"speed_x"))==500,"horizontal speed native -500..500 range");
 CHECK(!obs_property_visible(obs_properties_get(p,"outline_size")),"outline children hidden while OFF");
 CHECK(!obs_property_visible(obs_properties_get(p,"speed_x")),"axis speeds hidden when scrolling OFF");
 apply(p,src);uint32_t w0=obs_source_get_width(src),h0=obs_source_get_height(src);
 snap(src,"outline-off",NULL);
 obs_data_set_bool(s,"outline",true);modify(p,s,"outline");
 CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0,"manual outline marks preset Custom");
 CHECK(obs_property_visible(obs_properties_get(p,"outline_size")),"outline children exposed when ON");
 obs_data_set_int(s,"outline_size",4);modify(p,s,"outline_size");
 obs_data_set_int(s,"outline_color",0xFF0000FF);modify(p,s,"outline_color");
 obs_data_set_int(s,"outline_opacity",100);modify(p,s,"outline_opacity");apply(p,src);
 CHECK(obs_source_get_width(src)>w0&&obs_source_get_height(src)>h0,"outline adds natural measurement margins");
 long full=snap(src,"outline-red",NULL);
 int title_h=(int)obs_source_get_height(src)/2;
 CHECK(red_pixels("outline-red",0,title_h)>0&&red_pixels("outline-red",title_h,10000)>0,"real colored stroke rendered on title and body");
 CHECK(clear_edges("outline-red"),"stroke boundary leaves clear edge pixels; no clipping");
 obs_data_set_int(s,"outline_opacity",0);modify(p,s,"outline_opacity");apply(p,src);
 long no=snap(src,"outline-transparent",NULL);CHECK(no<full,"outline opacity 0 removes contour pixels");
 obs_data_set_int(s,"text_opacity",0);modify(p,s,"text_opacity");apply(p,src);
 CHECK(snap(src,"all-transparent",NULL)==0,"text and outline opacity independently reach zero");
 obs_data_set_int(s,"outline_opacity",100);modify(p,s,"outline_opacity");apply(p,src);
 CHECK(snap(src,"outline-only",NULL)>0,"stroke is real exterior contour even with fill invisible");
 obs_data_set_int(s,"outline_opacity",50);modify(p,s,"outline_opacity");apply(p,src);snap(src,"outline-half",NULL);
 CHECK(red_pixels("outline-half",0,10000)>0,"outline half opacity still renders");
 CHECK(max_alpha("outline-only")==255&&max_alpha("outline-half")>=126&&max_alpha("outline-half")<=129,"outline opacity 50% actually halves alpha coverage");
 obs_data_set_int(s,"text_opacity",100);modify(p,s,"text_opacity");
 obs_data_set_int(s,"outline_size",20);modify(p,s,"outline_size");apply(p,src);snap(src,"outline-max",NULL);
 CHECK(clear_edges("outline-max"),"maximum 20px stroke is not clipped");
 obs_data_set_bool(s,"outline",false);modify(p,s,"outline");apply(p,src);
 CHECK(obs_source_get_width(src)==w0&&obs_source_get_height(src)==h0,"outline OFF restores exact original dimensions");
 /* Short, asymmetric text; no overflow at all. Pixel correlation verifies
  * positive/negative and combined axes, not just that two frames differ. */
 obs_data_set_string(s,"a1_title","");modify(p,s,"a1_title");
 obs_data_set_string(s,"ui_body1","ABCxyz 中123");modify(p,s,"ui_body1");
 obs_data_set_int(s,"scroll_height",4096);modify(p,s,"scroll_height");
 axis_case(src,p,s,20,0,"left");axis_case(src,p,s,-20,0,"right");
 axis_case(src,p,s,0,20,"up");axis_case(src,p,s,0,-20,"down");
 axis_case(src,p,s,20,-15,"diagonal");axis_case(src,p,s,20,15,"diagonal-left-up");axis_case(src,p,s,-20,15,"diagonal-right-up");axis_case(src,p,s,-20,-15,"diagonal-right-down");axis_case(src,p,s,0,0,"stop");
 obs_data_set_double(s,"speed_x",40);modify(p,s,"speed_x");apply(p,src);
 obs_data_set_bool(s,"ui_paused",true);modify(p,s,"ui_paused");
 unsigned h1,h2;snap(src,"axis-paused-a",&h1);Sleep(220);snap(src,"axis-paused-b",&h2);CHECK(h1==h2,"pause freezes axis scrolling too");
 obs_data_set_bool(s,"ui_paused",false);modify(p,s,"ui_paused");
 obs_data_set_bool(s,"scroll_limit_width",true);modify(p,s,"scroll_limit_width");obs_data_set_int(s,"scroll_width",100);modify(p,s,"scroll_width");apply(p,src);
 CHECK(obs_source_get_width(src)==100,"axis viewport width limit crops rather than reflows");
 obs_data_set_bool(s,"scrolling",false);modify(p,s,"scrolling");apply(p,src);
 CHECK(obs_source_get_width(src)>100&&!obs_property_visible(obs_properties_get(p,"speed_x")),"scroll OFF restores natural width and hides speeds");
 obs_data_set_bool(s,"scrolling",true);modify(p,s,"scrolling");
 obs_data_set_double(s,"speed_x",-37);modify(p,s,"speed_x");obs_data_set_double(s,"speed_y",21);modify(p,s,"speed_y");obs_data_set_bool(s,"outline",true);modify(p,s,"outline");obs_data_set_int(s,"outline_size",3);modify(p,s,"outline_size");apply(p,src);
 fprintf(g_log,"140 before save\n");fflush(g_log);
 obs_data_t *saved=obs_save_source(src);fprintf(g_log,"140 after save %p\n",(void*)saved);fflush(g_log);
 obs_data_erase(saved,"uuid");obs_data_set_string(saved,"name","nb140-reloaded");
 const char *json=obs_data_get_json(saved);char path[MAX_PATH];snprintf(path,sizeof(path),"%s\\reload140.json",outdir);FILE *jf=fopen(path,"w");if(jf){fputs(json,jf);fclose(jf);}
 fprintf(g_log,"140 before load json length=%zu heap=%d\n",strlen(json),HeapValidate(GetProcessHeap(),0,NULL));fflush(g_log);
 obs_data_t *parsed=obs_data_create_from_json(json);
 obs_data_release(saved);saved=parsed;
 obs_source_t *re=obs_load_source(saved);fprintf(g_log,"140 loaded %p\n",(void*)re);fflush(g_log);
 obs_source_load2(re);fprintf(g_log,"140 load2 done\n");fflush(g_log);obs_data_release(saved);
 obs_data_t *l=live_settings(re);CHECK(obs_data_get_bool(l,"scroll_axis")&&obs_data_get_double(l,"speed_x")==-37&&obs_data_get_double(l,"speed_y")==21,"axis values and explicit new mode survive save/reload");
 CHECK(obs_data_get_bool(l,"outline")&&obs_data_get_int(l,"outline_size")==3,"outline survives save/reload");obs_data_release(l);obs_source_release(re);
 /* Legacy saved settings keep speed=51 with its old top/bottom-dwell meaning,
  * even after opening every modified callback. */
 init=obs_data_create();obs_data_set_bool(init,"scrolling",true);obs_data_set_int(init,"speed",51);
 obs_source_t *old=obs_source_create("obs_noticeboard","nb140-old13",init,NULL);obs_data_release(init);obs_properties_t *op=obs_source_properties(old);obs_data_t *os=obs_source_get_settings(old);obs_properties_apply_settings(op,os);
 CHECK(!obs_data_get_bool(os,"scroll_axis")&&obs_data_get_int(os,"speed")==51,"opening 1.3 scroll never reinterprets legacy speed");
 CHECK(obs_property_visible(obs_properties_get(op,"legacy_scroll_info")),"legacy behavior is explicitly explained in UI");
 obs_data_set_double(os,"speed_x",15);modify(op,os,"speed_x");CHECK(obs_data_get_bool(os,"scroll_axis")&&obs_data_get_int(os,"speed")==51,"editing axis opts into new mode without overwriting old speed");
 obs_properties_destroy(op);obs_data_release(os);obs_source_release(old);
 /* Six palettes + six scene styles with identical content; no content, playback, scrolling,
  * transform, or publication side effects from merely choosing a preset. */
 obs_data_set_bool(s,"scrolling",false);modify(p,s,"scrolling");obs_data_set_bool(s,"scroll_limit_width",false);modify(p,s,"scroll_limit_width");
 obs_data_set_int(s,"count",2);obs_data_set_string(s,"a1_title","公告标题");obs_data_set_string(s,"a1_body","欢迎来到直播间\n友善交流，享受今晚");obs_data_set_string(s,"a2_title","第二条");obs_data_set_string(s,"a2_body","欢迎来到直播间\n友善交流，享受今晚");
 modify(p,s,"a1_body");modify(p,s,"a2_body");apply(p,src);select_live(p,s,2);
 obs_scene_t *scene=obs_scene_create("nb140-preset-isolated");obs_sceneitem_t *item=obs_scene_add(scene,src);struct vec2 sc,pos;vec2_set(&sc,.7f,1.2f);vec2_set(&pos,23,77);obs_sceneitem_set_scale(item,&sc);obs_sceneitem_set_pos(item,&pos);
 const char *ids[]={"warm","ivory","sakura","ink","glass","violet","game","chat","caption","alert","quiet","note"};
 const int sizes[]={28,28,28,28,28,28,24,26,36,40,22,30};
 const int opacity[]={0,0,0,0,0,0,28,72,58,96,0,95};
 const char *faces[]={"Microsoft YaHei","Microsoft YaHei","Microsoft YaHei","Microsoft YaHei","Microsoft YaHei","Microsoft YaHei","Microsoft YaHei UI","DengXian","SimHei","Microsoft YaHei","Microsoft YaHei UI","KaiTi"};
 const int stroke[]={3,3,3,3,3,3,2,0,2,2,1,0};
 unsigned hashes[12];
 for(int i=0;i<12;i++) {
  const char *key=i<6?"color_preset":"scheme_preset";
  l=live_settings(src);char prev[64];snprintf(prev,sizeof(prev),"%s",obs_data_get_string(l,key));obs_data_release(l);
  obs_data_set_string(s,key,ids[i]);modify(p,s,key);
  l=live_settings(src);CHECK(strcmp(obs_data_get_string(l,key),prev)==0,"choosing preset does not publish");obs_data_release(l);
  CHECK(obs_data_get_int(s,"count")==2&&strcmp(obs_data_get_string(s,"a2_body"),"欢迎来到直播间\n友善交流，享受今晚")==0,"preset preserves announcement count and body");
  obs_data_t *f=obs_data_get_obj(s,"body_font");CHECK(obs_data_get_int(f,"size")==sizes[i]&&obs_data_get_int(s,"bg_opacity")==opacity[i],"color preset preserves typography/opacity; scene preset applies specification");CHECK(strcmp(obs_data_get_string(f,"face"),faces[i])==0&&obs_data_get_bool(s,"outline")== (stroke[i]>0),"preset font face and outline specification match");obs_data_release(f);
  CHECK(obs_data_get_double(s,"speed_x")==-37&&obs_data_get_double(s,"speed_y")==21&&!obs_data_get_bool(s,"scrolling"),"preset never changes axis speed or scrolling enablement");
  apply(p,src);char file[64];snprintf(file,sizeof(file),"preset-%s",ids[i]);CHECK(snap(src,file,&hashes[i])>100,"complete preset renders");
  l=live_settings(src);CHECK(strcmp(obs_data_get_string(l,key),ids[i])==0,"preset selection and applied snapshot agree");obs_data_release(l);
  struct vec2 a,b;obs_sceneitem_get_scale(item,&a);obs_sceneitem_get_pos(item,&b);CHECK(a.x==.7f&&a.y==1.2f&&b.x==23&&b.y==77,"preset never changes native scale or position");
  saved=obs_save_source(src);obs_data_t *priv=obs_data_get_obj(saved,"private_settings");CHECK(obs_data_get_int(priv,"cur_slot")==2,"preset keeps current playback item");obs_data_release(priv);obs_data_release(saved);
 }
 for(int i=0;i<12;i++)for(int j=i+1;j<12;j++)CHECK(hashes[i]!=hashes[j],"preset rendered comparison is distinct");
 obs_data_t *f=obs_data_get_obj(s,"body_font"),*fc=obs_data_create();obs_data_apply(fc,f);obs_data_set_int(fc,"size",31);obs_data_set_obj(s,"body_font",fc);obs_data_release(fc);obs_data_release(f);modify(p,s,"body_font");
 CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0,"manual font edit returns preset to Custom");apply(p,src);
 obs_properties_t *np=obs_source_properties(src);obs_properties_apply_settings(np,s);CHECK(strcmp(obs_data_get_string(s,"scheme_preset"),"custom")==0,"Custom survives property reconstruction");obs_properties_destroy(np);
 obs_source_remove(obs_scene_get_source(scene));obs_scene_release(scene);obs_properties_destroy(p);obs_data_release(s);obs_source_release(src);
}
int main(int argc, char **argv)
{
	if (argc < 4) {
		printf("usage: harness <plugin.dll> <plugin-data-dir> <outdir>\n");
		return 2;
	}
	outdir = argv[3];
	char lp[MAX_PATH];
	snprintf(lp, sizeof(lp), "%s\\harness-log.txt", outdir);
	g_log = fopen(lp, "w");
	base_set_log_handler(log_handler, NULL);
 SetUnhandledExceptionFilter(on_exception);

	char cfg[MAX_PATH];
	snprintf(cfg, sizeof(cfg), "%s\\obs-config", outdir);
	CreateDirectoryA(cfg, NULL);
	CHECK(obs_startup("zh-CN", cfg, NULL), "obs_startup");

	struct obs_video_info ovi = {0};
	ovi.graphics_module = "./libobs-d3d11"; /* 带斜杠才会按当前目录（OBS bin\\64bit）加载 */
	ovi.fps_num = 60;
	ovi.fps_den = 1;
	ovi.base_width = ovi.output_width = 1280;
	ovi.base_height = ovi.output_height = 720;
	ovi.output_format = VIDEO_FORMAT_NV12;
	ovi.gpu_conversion = true;
	ovi.colorspace = VIDEO_CS_709;
	ovi.range = VIDEO_RANGE_PARTIAL;
	ovi.scale_type = OBS_SCALE_BICUBIC;
	int vr = obs_reset_video(&ovi);
	CHECK(vr == OBS_VIDEO_SUCCESS, "obs_reset_video (D3D11)");
	if (vr != OBS_VIDEO_SUCCESS)
		return 1;

	obs_module_t *mod = NULL;
	int mr = obs_open_module(&mod, argv[1], argv[2]);
	CHECK(mr == MODULE_SUCCESS, "obs_open_module(obs-noticeboard.dll)");
	if (mr != MODULE_SUCCESS)
		return 1;
	CHECK(obs_init_module(mod), "obs_init_module");

	const char *dn = obs_source_get_display_name("obs_noticeboard");
	fprintf(g_log, "display name: %s\n", dn ? dn : "(null)");
	CHECK(dn && strcmp(dn, "公告板") == 0, "来源列表名字是「公告板」");


 if(getenv("NB_ONLY140")){test_140();obs_shutdown();fprintf(g_log,"RESULT: %s (%d failures)\n",failures?"FAILED":"ALL PASSED",failures);fclose(g_log);return failures?1:0;}
 /* A separate view copy models the 500ms OBS property update debounce. */
 obs_source_t *src=obs_source_create("obs_noticeboard","nb-130-headless",NULL,NULL);
 CHECK(src!=NULL,"source create"); Sleep(200);
 obs_properties_t *p=obs_source_properties(src);
 obs_data_t *actual=obs_source_get_settings(src);
 obs_data_t *s=obs_data_create_from_json(obs_data_get_json_with_defaults(actual));
 obs_data_release(actual);
 obs_properties_apply_settings(p,s);
 CHECK(visible_editors(p)==1,"only one announcement editor visible");
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_edit"))==1,"initial draft selector");
 CHECK(!obs_property_visible(obs_properties_get(p,"a1_body")),"large body editor hidden by default");
 CHECK(obs_property_visible(obs_properties_get(p,"ui_body1")),"compact single line body visible");
 CHECK(!obs_property_visible(obs_properties_get(p,"grp_adv")),"advanced hidden by default");
 CHECK(!obs_properties_get(p,"width")&&!obs_properties_get(p,"auto_height"),"no fixed size or drag-reflow controls");
 CHECK(!obs_data_get_bool(s,"scrolling"),"scrolling defaults OFF");
 /* Add TWO new announcements without reconstructing the properties object. */
 button(p,src,"add_item");
 CHECK(obs_data_get_int(s,"count")==2&&obs_data_get_int(s,"ui_edit")==2,"add selects second draft");
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_edit"))==2,"new blank draft immediately selectable");
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_current"))==1,"unapplied draft NOT published in live list");
 obs_data_set_string(s,"a2_title","第二条");modify(p,s,"a2_title");
 obs_data_set_string(s,"ui_body2","第二条第一行\\n第二行\\\\n保留字面反斜杠");modify(p,s,"ui_body2");
 CHECK(strcmp(obs_data_get_string(s,"a2_body"),"第二条第一行\n第二行\\n保留字面反斜杠")==0,"compact editor lossless newline/backslash decode");
 button(p,src,"add_item");
 CHECK(obs_data_get_int(s,"ui_edit")==3&&visible_editors(p)==1,"second add selects third, one editor only");
 obs_data_set_string(s,"a3_title","第三条");modify(p,s,"a3_title");
 obs_data_set_string(s,"ui_body3","第三条内容");modify(p,s,"ui_body3");
 obs_data_t *l=live_settings(src);
 CHECK(obs_data_get_int(l,"count")==1,"editing and adding do not publish");obs_data_release(l);
 /* Immediate Apply, no source update and no 500ms delay. */
 apply(p,src);
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_current"))==3,"Apply refreshes SAME live list to all three");
 l=live_settings(src);
 CHECK(obs_data_get_int(l,"count")==3&&strcmp(obs_data_get_string(l,"a3_body"),"第三条内容")==0,"Apply includes most recent typed text");obs_data_release(l);
 select_live(p,s,1); uint32_t firstw=obs_source_get_width(src);
 select_live(p,s,2);
 CHECK(obs_source_get_width(src)!=firstw,"switch uses selected announcement natural dimensions, not longest canvas");
 obs_data_t *saved=obs_save_source(src),*priv=obs_data_get_obj(saved,"private_settings");
 CHECK(obs_data_get_int(priv,"cur_slot")==2,"live selector switches second");
 obs_data_release(priv);obs_data_release(saved);
 select_live(p,s,3);
 saved=obs_save_source(src);priv=obs_data_get_obj(saved,"private_settings");
 CHECK(obs_data_get_int(priv,"cur_slot")==3,"live selector switches newly added third");
 obs_data_release(priv);obs_data_release(saved);
 obs_data_set_int(s,"ui_edit",2);modify(p,s,"ui_edit");
 obs_data_set_bool(s,"ui_multiline",true);modify(p,s,"ui_multiline");
 CHECK(obs_property_visible(obs_properties_get(p,"a2_body"))&&!obs_property_visible(obs_properties_get(p,"ui_body2")),"optional multiline editor");
 obs_data_set_string(s,"a2_body","多行编辑\n完整保存\n");modify(p,s,"a2_body");
 obs_data_set_bool(s,"ui_multiline",false);modify(p,s,"ui_multiline");
 CHECK(strcmp(obs_data_get_string(s,"ui_body2"),"多行编辑\\n完整保存\\n")==0,"multiline to compact preserves trailing newline");
 apply(p,src);
 button(p,src,"delete_item");
 CHECK(obs_data_get_int(s,"count")==2&&strcmp(obs_data_get_string(s,"a2_body"),"第三条内容")==0,"delete compacts draft without losing next item");
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_current"))==3,"draft deletion leaves live untouched");
 apply(p,src);
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_current"))==2,"delete Apply refreshes live list");
 select_live(p,s,2);
 /* Save/reload both draft and applied snapshots independently. */
 obs_data_set_string(s,"ui_body2","未应用草稿");modify(p,s,"ui_body2");
 obs_source_update(src,s);Sleep(150);
 saved=obs_save_source(src);
 obs_data_erase(saved,"uuid");obs_data_set_string(saved,"name","nb-130-reload");
 obs_source_t *re=obs_load_source(saved);obs_source_load2(re);Sleep(200);
 actual=obs_source_get_settings(re);l=live_settings(re);
 CHECK(strcmp(obs_data_get_string(actual,"a2_body"),"未应用草稿")==0,"save/reload preserves draft");
 CHECK(obs_data_get_int(l,"count")==2&&strcmp(obs_data_get_string(l,"a2_body"),"第三条内容")==0,"save/reload preserves applied announcements separately");
 obs_data_t *saved2=obs_save_source(re);priv=obs_data_get_obj(saved2,"private_settings");
 CHECK(obs_data_get_int(priv,"cur_slot")==2,"save/reload restores selected live announcement");
 obs_data_release(priv);obs_data_release(saved2);obs_data_release(actual);obs_data_release(l);obs_data_release(saved);obs_source_release(re);
 button(p,src,"revert");
 CHECK(strcmp(obs_data_get_string(s,"a2_body"),"第三条内容")==0,"Revert updates view settings and compact body");
 /* Isolated natural sizing checks: explicit lines, fonts, no drag writes. */
 obs_data_set_int(s,"count",1);obs_data_set_string(s,"a1_title","");modify(p,s,"a1_title");
 obs_data_set_string(s,"ui_body1","短行");modify(p,s,"ui_body1");
 obs_data_set_double(s,"fade",0);modify(p,s,"fade");
 apply(p,src); uint32_t wshort=obs_source_get_width(src),hone=obs_source_get_height(src);
 CHECK(wshort>40&&wshort<640,"short text shrinks native width below old fixed 640");
 obs_data_set_string(s,"ui_body1","这是一行更长的公告内容");modify(p,s,"ui_body1");apply(p,src);
 uint32_t wlong=obs_source_get_width(src);
 CHECK(wlong>wshort,"natural width grows with content");
 obs_data_set_string(s,"ui_body1","短行\\n短行");modify(p,s,"ui_body1");apply(p,src);
 CHECK(obs_source_get_width(src)==wshort&&obs_source_get_height(src)>hone,"explicit newline grows height, not width");
 obs_data_t *font=obs_data_get_obj(s,"body_font");obs_data_set_int(font,"size",56);obs_data_set_obj(s,"body_font",font);obs_data_release(font);modify(p,s,"body_font");apply(p,src);
 CHECK(obs_source_get_width(src)>wshort&&obs_source_get_height(src)>hone+40,"font size grows width and line height");
 CHECK(snap(src,"01-natural",NULL)>100,"natural card renders pixels");
 obs_scene_t *scene=obs_scene_create("nb130-isolated-scene");obs_sceneitem_t *item=obs_scene_add(scene,src);
 struct vec2 scale;vec2_set(&scale,.65f,1.35f);obs_sceneitem_set_scale(item,&scale);
 uint32_t wn=obs_source_get_width(src),hn=obs_source_get_height(src);
 long long legacy_width=obs_data_get_int(s,"width");Sleep(1000);
 struct vec2 after;obs_sceneitem_get_scale(item,&after);
 actual=obs_source_get_settings(src);l=live_settings(src);
 CHECK(after.x==.65f&&after.y==1.35f,"native nonuniform scene scale NOT reset after idle ticks");
 CHECK(obs_source_get_width(src)==wn&&obs_source_get_height(src)==hn,"native scaling never reflows source dimensions");
 CHECK(obs_data_get_int(actual,"width")==legacy_width&&obs_data_get_int(l,"width")==legacy_width,"drag never writes draft or applied width");
 obs_data_release(actual);obs_data_release(l);
 /* Body exceeds a small scroll viewport: off grows, on scrolls. */
 obs_data_set_string(s,"ui_body1","一行\\n二行\\n三行\\n四行\\n五行\\n六行");modify(p,s,"ui_body1");
 obs_data_set_int(s,"scroll_height",180);modify(p,s,"scroll_height");
 obs_data_set_int(s,"speed",120);modify(p,s,"speed");obs_data_set_double(s,"top_dwell",0);modify(p,s,"top_dwell");
 obs_data_set_bool(s,"scrolling",false);modify(p,s,"scrolling");apply(p,src);
 CHECK(obs_source_get_height(src)>180,"scroll OFF expands height beyond configured viewport");
 unsigned h1,h2;snap(src,"02-off-a",&h1);Sleep(400);snap(src,"03-off-b",&h2);
 CHECK(h1==h2,"scroll OFF remains visually static");
 obs_data_set_bool(s,"scrolling",true);modify(p,s,"scrolling");obs_data_set_bool(s,"scroll_axis",false);apply(p,src);
 CHECK(obs_source_get_height(src)==180,"scroll ON caps tall card height");
 snap(src,"04-on-a",&h1);Sleep(450);snap(src,"05-on-b",&h2);CHECK(h1!=h2,"scroll ON actually advances overflowing body pixels");
 obs_data_set_bool(s,"ui_paused",true);modify(p,s,"ui_paused");
 snap(src,"06-pause-a",&h1);Sleep(300);snap(src,"07-pause-b",&h2);CHECK(h1==h2,"pause freezes scroll");
 obs_data_set_bool(s,"ui_paused",false);modify(p,s,"ui_paused");
 obs_data_set_string(s,"ui_body1","短");modify(p,s,"ui_body1");apply(p,src);
 CHECK(obs_source_get_height(src)<180,"scroll ON short text still auto sizes");
 snap(src,"08-short-a",&h1);Sleep(300);snap(src,"09-short-b",&h2);CHECK(h1==h2,"short text does not scroll");
 obs_sceneitem_get_scale(item,&after);CHECK(after.x==.65f&&after.y==1.35f,"Apply and scrolling preserve native scale");
 /* Preset changed via same view applies; opening properties doesn't overwrite. */
 obs_data_set_string(s,"color_preset","ivory");modify(p,s,"color_preset");
 CHECK(obs_data_get_int(s,"bg_color")!=0xFF1C1418,"preset selection still applies");
 apply(p,src);
 long long bg=obs_data_get_int(s,"bg_color");
 obs_properties_t *p2=obs_source_properties(src);obs_properties_apply_settings(p2,s);
 CHECK(obs_data_get_int(s,"bg_color")==bg,"reopen properties preserves chosen preset");obs_properties_destroy(p2);
 /* Disabled and blank announcements are editable, never published as phantom rows. */
 button(p,src,"add_item");obs_data_set_string(s,"a2_title","停用公告");modify(p,s,"a2_title");
 obs_data_set_bool(s,"a2_on",false);modify(p,s,"a2_on");apply(p,src);
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_edit"))==2&&obs_property_list_item_count(obs_properties_get(p,"ui_current"))==1,"disabled announcement stays editable but not live");
 obs_data_set_bool(s,"a2_on",true);modify(p,s,"a2_on");apply(p,src);
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_current"))==2,"re-enable refreshes live dropdown in same properties");

 const char *presets[]={"warm","ivory","sakura","ink","glass","violet"};
 for(int i=0;i<6;i++){obs_data_set_string(s,"color_preset",presets[i]);modify(p,s,"color_preset");apply(p,src);CHECK(snap(src,presets[i],NULL)>100,"preset renders");}
 /* 1.2 settings retain every old announcement even though fixed dimensions
  * are now ignored. No user scene collection is opened or modified. */
 obs_data_t *old=obs_data_create();obs_data_set_int(old,"count",2);obs_data_set_int(old,"width",1200);obs_data_set_int(old,"height",900);obs_data_set_bool(old,"auto_height",false);
 obs_data_set_string(old,"a1_title","");obs_data_set_string(old,"a1_body","旧公告第一行\n旧公告第二行");obs_data_set_string(old,"a2_body","原有第二条");
 obs_source_t *mig=obs_source_create("obs_noticeboard","nb130-migration",old,NULL);obs_data_release(old);
 actual=obs_source_get_settings(mig);l=live_settings(mig);
 CHECK(strcmp(obs_data_get_string(actual,"a1_body"),"旧公告第一行\n旧公告第二行")==0&&strcmp(obs_data_get_string(l,"a2_body"),"原有第二条")==0,"legacy multiline and second announcement retained");
 CHECK(!obs_data_get_bool(actual,"scrolling")&&obs_source_get_width(mig)<1200&&obs_source_get_height(mig)<900,"legacy fixed size ignored, natural default, no implicit scrolling");
 obs_data_release(actual);obs_data_release(l);obs_source_release(mig);
 char huge[12001];memset(huge,'W',sizeof(huge)-1);huge[sizeof(huge)-1]=0;
 obs_data_set_string(s,"ui_body1",huge);modify(p,s,"ui_body1");apply(p,src);
 CHECK(obs_source_get_width(src)<=4096&&obs_source_get_height(src)<=4096,"extreme line obeys render safety limits");
 l=live_settings(src);CHECK(strlen(obs_data_get_string(l,"a1_body"))==12000,"render safety never truncates saved text");obs_data_release(l);
 while(obs_data_get_int(s,"count")<20)button(p,src,"add_item");
 CHECK(obs_property_list_item_count(obs_properties_get(p,"ui_edit"))==20&&!obs_property_enabled(obs_properties_get(p,"add_item")),"20-item limit, selector complete, add disabled");
 button(p,src,"add_item");CHECK(obs_data_get_int(s,"count")==20&&visible_editors(p)==1,"extra Add safe; still only one editor");
 obs_properties_destroy(p);obs_data_release(s);obs_source_remove(obs_scene_get_source(scene));obs_scene_release(scene);obs_source_release(src);
 test_140();
 test_141();
 test_142();
 obs_wait_for_destroy_queue();
 obs_shutdown();
 fprintf(g_log,"RESULT: %s (%d failures)\n",failures?"FAILED":"ALL PASSED",failures);fclose(g_log);
 return failures?1:0;
}
