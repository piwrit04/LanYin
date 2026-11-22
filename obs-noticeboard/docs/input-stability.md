# 1.4.2 — 连续输入稳定性修复

## 实际根因，不是自动发布

对照本地 OBS 32.2.2 源码：

- `shared/properties-view/properties-view.cpp:2040-2140`：`WidgetInfo::ControlChanged()` 先更新对应 settings，调用原生 visual-update callback，再调用插件 `obs_property_modified()`。如果 modified 返回 true，就排队调用整个 `RefreshProperties()`。
- 同文件 `112-162`：`RefreshProperties()` 清空 WidgetInfo，旧容器 `deleteLater()`，新建 QWidget 并重新创建全部属性控件；最后只尝试恢复焦点。正在拖动的旧滑块会被销毁，鼠标抓取无法延续；输入框的选择/光标状态也无法靠简单 setFocus 完整恢复。
- `frontend/dialogs/OBSBasicProperties.cpp:69-75` 把 `obs_source_update` 作为 `visUpdateCb`；`properties-view.cpp:2127-2132` 每个值变化都会调用它。这是原生草稿/预览更新路径，不能误称所有更新都等 500ms。源代码中确有 500ms update_timer，但此属性窗口的 special callback 为 nullptr。
- `OBSBasicProperties.cpp:95-96,290-293` 把来源的 `update_properties` signal 连接到 `ReloadProperties()`；插件没有调用该 API，也没有 Qt 控件 hook。

1.4.1 的颜色/方案签名每次检测到值改变都会同时 `refresh = true`，即使该预设已经是 Custom。这使每次滑块刻度、轮廓大小、透明度等变化都重建整个面板。水平/垂直速度分支也对每次新值设置 true，而不只是在旧滚动迁移时。并且每个键入都无条件执行 `ui_sync`，反复清空/重填公告列表并更新状态元数据。

## 修复范围

- 所有普通 int/float/color/font/text 连续编辑都返回 false，包括从已命名预设第一次手调。
- 签名照常立即更新，各自的 `color_preset / scheme_preset` 在数据里立即变为 Custom，但不为更新标签而销毁正在输入的控件。
- 普通输入不再调用 `ui_sync`，不清空列表、不逐字刷新 status，不调用 `obs_source_update_properties`、不额外调用整来源 update，不加 debounce/timer。
- 速度编辑仍可显式迁移旧滚动，但不因此重建面板。已有旧 speed 字段含义保留。
- 只有确实改变依赖控件的编辑公告选择、展开多行、高级设置、轮廓开关、滚动开关、播放方式、滚动宽度限制等结构操作，或明确换预设/按钮操作，才刷新必要的面板。相同结构值重复 callback 返回 false。
- 暂停、当前播放条和公告启用状态没有控件结构依赖，不再要求重建。

## 可见标签延后是有意取舍

原生公开 properties API 不能在保留现有控件的同时把另一处 combo 的当前项或 status 文本强制同步到 Qt。为优先保证拖动/光标连续性，手调后的预设标签、状态、公告选择列表中的标题在下一次切换编辑/展开面板/按钮操作或重新打开属性时刷新；数据中的真实值和 Custom 标记立即更新。若当前仍显示旧预设，需重新套用同名预设时可先选 Custom 再选目标预设，或重开窗口。

这不是自动发布：原生仍保存草稿，只有“应用到直播”才发布。确定只保存草稿；直播/草稿隔离、两组独立预设及旧数据兼容不变。

## 测试边界

`tests/obs_harness.c` 真实加载 OBS 32.2.2、插件 DLL、D3D11 和 Windows GDI。新测试按原生顺序，先 `obs_source_update` 再调用 modified；覆盖所有普通滑块/数字框、五种颜色、四种不透明度、轮廓大小、两个字体字号、水平/垂直速度、标题、紧凑和多行正文、逐段中文输入。

逐次断言 modified 返回 false、来源没有发出 `update_properties` signal、普通输入不修改 status/选择列表元数据；命名预设首次手调即记录正确的独立 Custom；大量编辑和原生 draft update 不发布；显式 Apply 仍包含最后输入。既有完整 1.4.1 回归也保留。

这是 headless API/渲染回归和真实 Qt 源码路径核对，不是实际鼠标拖动/IME/焦点 GUI 实测；未通过 Shell 驱动桌面 GUI。

最终实测：安装前 3422 PASS / 0 FAIL；安装后同版完整回归 3422 PASS / 0 FAIL，再把新增输入序列限定为各属性真实类型、合法范围和原生步进，最终安装路径完整回归 3454 PASS / 0 FAIL。最后一次含所有既有 1006 检查项，无 remaining-source / double-destroy 警告。这里只发布测试源与汇总，不附运行环境日志。
