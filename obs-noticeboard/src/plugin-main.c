/* SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 星澪
 */
#include <obs-module.h>
#include "nb-version.h"

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-noticeboard", "zh-CN")
OBS_MODULE_AUTHOR("星澪")

extern struct obs_source_info nb_source_info;

MODULE_EXPORT const char *obs_module_name(void)
{
	return "公告板 Noticeboard";
}

MODULE_EXPORT const char *obs_module_description(void)
{
	return obs_module_text("Module.Description");
}

bool obs_module_load(void)
{
	obs_register_source(&nb_source_info);
	blog(LOG_INFO, "[公告板] obs-noticeboard %s 已加载", NB_VERSION);
	return true;
}
