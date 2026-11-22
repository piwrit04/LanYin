# OBS 32.2.2 属性窗口底部按钮调查

结论：本插件使用公开 libobs properties API，无法稳定、受支持地把「应用到直播」放到原生「确定」旁边。本版保留原位置，不挪到滚动区底部，不重写窗口，不改「确定只保存草稿」。

本地证据：
- `frontend/forms/OBSBasicProperties.ui:156-158`：原生 `QDialogButtonBox` 只声明 Cancel | Ok。
- `frontend/dialogs/OBSBasicProperties.cpp:59-77`：OBS 创建自己的 dialog/buttonBox，再把插件 properties 生成的 `OBSPropertiesView` 加到 propertiesLayout。
- 同文件 `:295-347`：AcceptRole 的撤销/保存、close 和 defer-update 属于 OBS 对话框自身；没有插件注册按钮扩展回调。
- `frontend/api/obs-frontend-api.h:118-119,151-172,237`：公开主窗口、工具菜单、dock、前端事件、打开来源属性窗口；没有获取某个属性对话框/buttonBox 或追加该按钮的方法。
- `libobs/obs-properties.h:217-221`：add_button2 只能增加 properties 表单中的按钮，没有 native-dialog footer 位置标志。
- `shared/properties-view/properties-view.cpp` 的 AddButton：把 properties 按钮放进自己的 form layout，不是对话框底部 buttonBox。

交叉核对公开文档（文档站目前显示 33.x，版本判断仍以本地 32.2.2 头文件/实现为准）：
- https://docs.obsproject.com/reference-properties
- https://docs.obsproject.com/reference-frontend-api
- https://doc.qt.io/qt-6/qdialogbuttonbox.html （Qt 自己有 addButton，但插件没有受支持的 OBS 属性对话框引用。）

搜索了公开用法及 X 的 Grok Bot/@bot 相关用法，未找到受支持的扩展口。通过 get_main_window 后扫描所有 Qt 子对象、event filter/hook 私有窗口或依赖 OBSBasicProperties 私有实现虽可能做出效果，但不满足稳定、范围可控的约束，因此未采用。若未来必须精确放置，需要单独设计由插件完整拥有的自定义 Qt 编辑窗口，而不是本次必要功能修订。
