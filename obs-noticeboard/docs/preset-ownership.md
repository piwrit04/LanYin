# 1.4.1 — 两组独立预设

## Native properties

| 实际 key | 下拉内容 | 独占修改范围 |
|---|---|---|
| `color_preset` | `custom` + `warm / ivory / sakura / ink / glass / violet` | 标题、正文、背景、边框和轮廓的 RGB 配色 |
| `scheme_preset` | `custom` + `game / chat / caption / alert / quiet / note` | 字体、字号、字重、文字/背景/边框/轮廓不透明度、轮廓开关和大小、边框宽度、圆角、内边距、标题间距、行高和对齐 |

这是两个真正的 OBS 下拉控件，不是将十二项放进一个 `preset` 下拉。旧 `preset` 数据键保留，界面不再展示它，也不再根据它重套样式。

**方案同样不改配色**。因此六种方案与六种配色可以独立组合。所有预设都不改公告内容、条数、当前条目、播放、滚动参数以及 OBS 场景位置/缩放，不自动发布草稿。

## Independent Custom signatures

- `color_signature` 仅包含 `title_color / text_color / bg_color / border_color / outline_color` 的低 24 位（RGB），不含不透明度或几何尺寸。
- `scheme_signature` 包含标题和正文字体 face/size/flags；`text_opacity / bg_opacity / outline / outline_size / outline_opacity / border_opacity / border_width / radius / padding / title_gap / line_height / align`。不含任何配色。
- 修改一种样式只使所属组变成 `custom`。不干扰另一组选择。
- `nb_keys` 同时保存两个 ID；「应用到直播」复制它们到独立的直播快照；「撤销草稿」恢复两者并刷新两套签名缓存。

## Upgrade safety

打开旧 1.3 / 1.4 数据时，如果新 ID 尚不存在，只按已有实际字段匹配相应组标签；不匹配就标为 `custom`。匹配时在临时 settings 上生成参考值，绝不把旧预设重套到来源，不覆盖任何旧字体、透明度、正文或播放状态。已明确保存的 `custom` 也不会被重新推断。旧 `preset` 键原值保留。

## Regression evidence

`tests/obs_harness.c` 复用真实 OBS 32.2.2 + Windows D3D11/GDI 离屏 harness；包含全部既有 1.3/1.4 回归，并增加：

- 两个 key 的确存在，每个恰好 6 套 + Custom，旧合并控件不存在；
- 6×6 完整交叉及反向选择，检查字体/大小/透明度/轮廓/布局和五种配色互不重设，双 ID 不互相失效；
- 每个组合实际应用并渲染，直播快照双 ID 与选择一致；
- 五种 RGB、四种不透明度及字体/轮廓开关/粗细手调，分别检查 Custom 归属；
- 不同直播/草稿双 ID 的 JSON 保存重载，撤销草稿及双 Custom 保存重载；
- 1.3 自定义旧数据、1.4 实际场景样式旧数据迁移不改变任何样式哈希/正文。

`previews/1.4.1-preset-contact-sheet.png` 是 36 个实际离屏 BMP 的统一自然比例对比；原始像素由测试 harness 输出，比较图保留共同自然比例。比较图不是属性窗口截图；未进行 GUI 人工布局/交互验证。
