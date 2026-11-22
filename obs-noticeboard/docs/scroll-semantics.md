# 1.4 双轴滚动语义

OBS 32.2.2 上游滚动实现：
https://raw.githubusercontent.com/obsproject/obs-studio/32.2.2/plugins/obs-filters/scroll-filter.c

原版 properties 的 speed_x/speed_y 均为 float slider，-500..500、步长 1。tick 累加 `offset += seconds * speed / base_size`；着色器采样 UV 加 offset，因此正值视觉向左/向上，负值向右/向下，单位像素/秒，0 停止并归零该轴。原版默认 loop=true，短来源也可循环，无需溢出。

本插件采用同样有符号速度和 UV wrap，限于公告正文（标题/卡片固定），速度归属于草稿快照，只有应用后影响直播。没有冒称本插件就是给整个 OBS 来源自动添加内置滤镜。

兼容：旧 `speed`/top_dwell/bottom_dwell 保持原溢出滚动语义；新增 `scroll_axis` 明确标记新方式，`speed_x`/`speed_y` 为独立字段。打开/加载旧配置不会迁移。用户调整新轴速度，或关闭再开启滚动，才采用新方式。旧模式期间有明确提示。
