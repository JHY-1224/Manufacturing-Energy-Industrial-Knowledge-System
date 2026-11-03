# 工作台截图复现与交互验收

final result: passed

验收日期：2026-11-01。验收范围是本轮六页工作台的参考格式、可用交互和响应式布局；不是 PostgreSQL/Milvus 集群或制造业务准确率验收。当前无未解决的 P0/P1/P2 视觉问题。品牌、业务文案、真实数据和基础设施名称采用本项目实际内容，不能把“passed”理解为两张截图逐像素相同。

## 视觉依据、视口与状态

源图目录：`data/runtime/reference-upgrade-b9f1cde4e87843298530ca449b77b588/references/`，保留用户八张原始截图。

| 页面 | 来源 | 源图 pixels | 最终实现截图 | CSS viewport / 实现 pixels |
|---|---|---|---|---|
| 总览 | 02.png | 1280×645 | `data/runtime/workbench-qa-20261101-delivery/dashboard-desktop.png` | 1280×645 |
| 上传 | 03.png | 1280×644 | `data/runtime/workbench-qa-20261101-delivery/upload-desktop.png` | 1280×644 |
| 文档 | 04.png | 1280×653 | `data/runtime/workbench-qa-20261101-delivery/documents-desktop.png` | 1280×653 |
| Chunk | 05.png | 1280×640 | `data/runtime/workbench-qa-20261101-delivery/chunks-desktop.png` | 1280×640 |
| Agent | 06.png | 1280×655 | `data/runtime/workbench-qa-20261101-delivery/agent-desktop.png` | 1280×655 |
| 对话局部 | 07.png | 1280×600 | Agent 消息区 | 来源为局部放大，不作为整页尺寸基准 |
| 检索调试 | 图文功能描述 | 无独立页面截图 | `data/runtime/workbench-qa-20261101-delivery/retrieval-desktop.png` | 1280×645，继承其余页面组件规范 |

所有桌面与移动截图均使用 Chromium、deviceScaleFactor=1，不缩放源图。移动实现为 390×844，参考未提供移动稿，按可操作性验收，不声称移动像素复刻。源图 05 已滚动裁掉大部分顶部；最终实现保留整页顶部，因此整体对比中的纵向位置差不能当作间距误差，Chunk 内容区另作对齐比较。

来源内容是课程文档及对话；实现内容是隔离验收库中的 3 个内置 Demo 和 2 个合成文档。截图统计来自 API，没有伪造 11 份文档或 151 个 Chunk。最终 Agent 截图为已完成的真实本地 Qwen 会话，滚动到回答末段；工具事件与来源不虚构为零。

## 同输入对比证据

最终合成对比文件均把源图与实现放在**同一张图片**中，先后查看并复核，不以分开看图冒充同屏比较：

- `data/runtime/workbench-qa-20261101-delivery/dashboard-comparison.png`
- `data/runtime/workbench-qa-20261101-delivery/upload-comparison.png`
- `data/runtime/workbench-qa-20261101-delivery/documents-comparison.png`
- `data/runtime/workbench-qa-20261101-delivery/chunks-comparison.png`
- `data/runtime/workbench-qa-20261101-delivery/agent-comparison.png`

局部比较：`table-focus-comparison.png` 核对表头、字体、操作按钮、行间分隔；`form-focus-comparison.png` 核对标签、输入框、上传图标与间距；`chunks-focus-comparison.png` 将源图 y=188 与实现 y=246 的双栏卡片顶部对齐，比较 1010×360 区域。均在上述 delivery 目录，三张局部图均已查看。Chunk 长 ID 和模型副本可从完整元数据展开，正文没有被截断保存。

移动截图：同目录 `*-mobile.png`；六页总览证据另见 `data/runtime/workbench-qa-20261101-approved/mobile-contact.png`。手机表格使用内部横向滚动，外层页面无横向溢出；会话/正文区域独立滚动。

## 对比过程与已修复问题

| 轮次 | 发现 | 修复 | 后验依据 |
|---|---|---|---|
| Pass 1 / blocked | [P1] 切换页面继承旧滚动位置，页头与品牌被裁掉 | hash 路由及初次刷新完成后回到顶部，截图等待真实数据和字体 | pass2 对比和 delivery 全页 |
| Pass 1 / blocked | [P2] 原全局样式使输入框增高、表头背景变灰；侧栏状态挤成三行 | 工作台显式输入高度、白色表头；状态分两行 | delivery form/table 局部图 |
| Pass 1 / blocked | [P2] 总览标题出现孤字换行 | 使用完整制造业务标题，约束标题宽度和行高 | pass2 / delivery dashboard |
| Pass 2 / blocked | [P2] 长 Chunk ID 撑高元数据区，压缩正文 | 单行省略、title 和完整元数据保留全 ID | delivery chunks |
| Pass 2 / blocked | [P2] 对话卡片最小高度过大，输入区超出桌面首屏 | 高度随视口变化、会话内部滚动、移动独立高度 | delivery agent，输入与发送可见 |
| Pass 2 / blocked | [P2] 小字和状态标签对比度偏低，参考本身也有该问题 | 保持蓝灰主题，适度加深正文、表头、主按钮和绿色状态 | delivery 全页和局部图 |
| Final review / blocked | [P2] 工具事件增长挤出来源；历史真实回答旁默认离线选项含义不明确 | 事件列表限高滚动，模式明确标注“下一轮” | delivery agent |
| Delivery / passed | 上述问题已复核，无新的 P0/P1/P2 | 保留实际品牌、数据和适用边界 | delivery 对比文件与 ui-result.json |

浏览器脚本早期另有两项测试等待问题：会话刷新后的断言早于异步返回；移动侧栏状态元素按设计隐藏而测试等待 visible。分别改为等待消息数量、检查已连接文字状态，未删除断言或降低业务验收要求。失败记录和前两轮截图保留。

## 五项视觉检查

1. **字体和层级**：系统无衬线栈，中文 PingFang SC / Microsoft YaHei 回退，英文 Inter / SF / Segoe UI 回退；标题 24px、主介绍 20px、卡片标题 16px、小标题 11px、正文 12–14px。参考没有提供字体文件，无法证实其准确字体，未私自下载未知字体。已核对行高、粗细、截断和中文换行，样式接近截图；保留系统字体的细微字宽差作为 P3。
2. **空间与布局**：240px 侧栏、18px 主间隙、主内容约 x=270、24px 白卡圆角、16px 模块间隔；四指标卡、上传双栏、Chunk 约 41/59 双栏、Agent 三栏结构与参考一致。源图不同页面存在 3–20px 顶部裁切差，本实现采用统一 12px 页面外边距。更多真实字段和行业选择是有意增补。
3. **颜色**：浅蓝灰背景、白卡、蓝边与蓝色主操作、绿色在线状态。小字/主操作有意提高对比度；不复刻参考中较淡的低对比文本。焦点边框和 reduced-motion 已实现。
4. **图像与图标**：参考 UI 没有照片/插画；使用官方 Element Plus Icons Vue 的同类图标，没有手绘 SVG、emoji 或截图铺底。品牌改为本项目 MI 字母标识与中文产品名；不冒充参考项目 RA。原截图只用于验收，没有嵌入产品页面。
5. **文案与数据**：制造领域文案、知识空间名称、实际 SQLite/index/agent 状态替代课程与未部署基础设施；内置 Demo 有标记，合成资料正文明确非工艺标准。离线/真实模式、当前 Chunk 与历史来源、软删除保留原件均说明真实行为。

## 交互、访问性与运行证据

- 内置浏览器启动因缺失 Node runtime 失败；用户明确批准后改用已有本地 Playwright/Chromium。没有读取用户浏览器登录资料或访问外部站点。
- 上传合成 Markdown → 解析 → Chunk 编辑 → 停用/启用 → 关键词检索 → Agent 多轮 → 询问上一问题 → 刷新恢复，均通过实际 UI。
- 两次真实 OCR + BGE 检索 + MLX SSE 通过；来源对应历史版本。后端重启后历史答案及来源逐项相等。
- 主页面和七个高级入口可打开；浏览器 pageerror/console error 列表为空。手机六页外层 scrollWidth 不超过 viewport。
- 弹窗具有对话语义、标签、初始焦点、Tab 焦点循环、Escape 和关闭后焦点恢复；编辑器初始焦点通过浏览器断言。确认删除沿用本地明确确认，未对真实业务数据执行删除。
- HTML/文档内容以文本呈现，不使用 v-html 执行上传内容。所有读写均接真实本地 API，断连/失败不填造统计或答案。
- 证据：`data/runtime/workbench-qa-20261101-final/ui-result.json`（完整交互），`data/runtime/workbench-qa-20261101-delivery/ui-result.json`（最终尺寸和页面），`data/runtime/workbench-qa-20261101-final/source-preservation.json`（原文件未丢失、旧 App.vue 未改）。

## 非阻断差异与后续边界

- [P3] 缺原字体源文件，系统字体字宽不能保证与截图逐像素一致；原截图和当前制造内容的长度也不同。
- [P3] 表格行数、真实来源数量、工具事件数量与参考课程库不同，因此不比较数据造成的页面高度差。
- 没有参考检索调试和移动页面截图；沿用同一视觉组件，已做功能与响应式验收。
- PostgreSQL/Milvus/Redis、视觉模型语义识读、图文向量、生产权限与永久清除未实装；这些属于 AGENTS.md 中的服务器/后续能力边界，不以本报告当作生产功能完成证明。

实施检查：上述 P1/P2 已修复并复拍；Python 93/93、前端 11/11、构建/编译/补丁检查通过。预览在 `http://127.0.0.1:8080/#/dashboard` 保留运行，使用隔离的合成验收库。


## 化妆品专属资料入库后的增量验收（2026-11-01）

final result: passed

本节是前述首次工作台验收之后的新增记录。当前网页已改用开发主库，显示 1,108 份总文档、2,244 个总 Chunk；化妆品专属筛选为 1,105 份文档。首次验收库与其截图保持独立保留。本轮没有变更原工作台的侧栏、配色、卡片体系或六页信息架构。

证据目录：`data/runtime/corpus-imports/cosmetics-20261101-024280f76c/ui-final/`。

- `documents-reference-viewport.png` 在 **1280×653** 复拍，`documents-reference-comparison.png` 将用户源图 04 与实现按相同尺寸并排；已打开同一张组合图比较。保留原侧栏、页头、介绍卡和表格卡，新增一行知识库/文件名筛选，表格因此下移，属于满足千份资料管理的有意增补。长训练文件名换行，完整知识库名、计数和操作按钮可读。
- `documents-top-desktop.png` 为 1280×900 首屏，专属标签、1,105 份/每页 50 份与真实文件数据可见。分页 23 页；实际前进/后退、文件名筛选、无结果空态通过。最早 `documents-desktop.png` 是点击分页后的底部状态，不能当作首屏截图，正确首屏以新增文件为准。
- `image-desktop.png`、`chunks-mobile.png` 已查看：原图按比例 contain 显示，来源说明与“未经核验、尚未视觉识读”标签可见；下载和 Chunk 操作仍可用。移动 390px 单列，没有外层横向溢出。ID 在窄屏省略但完整元数据仍可展开，不截断保存内容。
- `training-desktop.png` 已查看：文本训练参考采用独立标签，显示原 user/assistant 案例和来源；原 system 指令不进入证据正文。图文来源与训练参考的审核边界分别说明，没有冒充已确认工艺事实。
- `formats-result.json` 证明 Chromium 能解码实际 JPEG、WebP、PNG、GIF、AVIF 五种源图。GIF 预览可展示原文件，不代表已逐帧 OCR 或视觉识读。
- `result.json` 记录专属 BM25 命中、制造通用无命中、分页、图像和移动检查；浏览器 pageerror/console error 为空。文档页面首次加载实测约 2.4 秒，后续 hash 切换复用已加载快照；这些数值不是高并发性能承诺。

本轮浏览器工具再次受旧工作区符号链接限制，沿用用户此前批准的本机 Playwright，只访问 localhost；未读取用户登录资料或外部图片 URL。早期脚本有两项定位/等待错误（隐式 label 含 option 文本；切换页面后过早读取 options），修正为已存在的控件定位和等待真实选项，所有业务断言保留，失败记录仍在本地。

新增 P2 问题已修复：筛选无结果时增加明确空态，文档数减少时页码收敛；新上传也继承专属规则，避免界面所示专属范围与后端不一致。当前无未解决 P0/P1/P2 视觉问题。P3：前端分页仍需完整快照，服务端分页/虚拟列表按后续规模升级；原参考与真实资料内容长度不同，不做虚假的逐像素一致声明。

最终检查：Python 101/101、前端状态/SSE 11/11、146 个 Python 文件内存编译、Vite 保留 dist 构建、补丁空白检查通过。实际本地 BGE+Qwen 对导入的配套文字做了一次带来源 SSE 验收，详见 AGENTS.md；不等于图片理解或工艺准确率验收。


## P2 分页与任务中心验收（2026-11-01）

final result: passed

本轮保留既有截图布局，新增后台任务入口并把列表改为服务端分页。仍沿用用户已批准的本地 Playwright/Chromium，只访问本机，未使用浏览器登录资料。主库仅作读取验收；可变操作在独立合成库执行。

- 来源视觉：`data/runtime/reference-upgrade-b9f1cde4e87843298530ca449b77b588/references/04.png`，1280×653。实现：`data/runtime/p2-index-jobs/c654e673af0441458cd2d78db565703e/ui-acceptance/documents-final-desktop.png`，相同 CSS viewport/pixels，deviceScaleFactor=1。都为文档页第一屏；来源是课程库11份，实现是主库1108份、筛选专属1105份，数据内容有明确差异。
- 同屏整体比较已打开复核：同目录 `documents-reference-comparison.png`（上下合并，不拉伸）；局部 `table-reference-focus.png` 对齐表头、行文本和操作列，检查字号、按钮、分隔与换行。新增知识库/文件名筛选、任务入口增加纵向高度，属于授权功能需要，不冒充截图逐像素相同。
- 任务页无独立用户参考稿，沿用现有白色圆角卡片、蓝色主操作和双栏结构；`tasks-final-desktop.png` / `tasks-mobile.png` 显示真实2,244项完成记录。`chunks-final-desktop.png` 保留列表/详情结构，远程选择器显示分页状态。移动端390×844无外层横向溢出。
- 字体：继承既有中文系统字体、深色标题与灰蓝辅助文字；未改变字号体系。较长训练文件名在单元格换行，未遮住操作列。
- 间距/布局：保持约240px侧栏、16px卡片间隔、原有圆角与内边距；新增任务两栏在移动端堆叠。截图对比时已回到页面顶部，避免滚动状态造成伪差异。
- 色彩：背景、边框、蓝色按钮沿用已有tokens，完成/失败文案与颜色同时呈现。任务状态不以颜色作为唯一信息。
- 图像/图标：复用既有 Element Plus 图标，未引入占位插图或新图片资产；原图预览逻辑未变。
- 文案：如实区分真实BGE和离线Hash、暂停当前小批后生效、取消保留结果、资料变化需新任务。没有把文本向量预建标成千图视觉理解。
- 交互证据 `result.json`：分页请求均含page参数、每页上限、空态、移动页、任务明细分页/筛选、暂停继续、后台上传解析、失败重试/取消、跨页来源跳转、搜索清空详情；console/pageerror为空。`duplicate-result.json` 验证重复上传不新建任务、不丢人工编辑；`task-focus-result.json` 验证旧任务复用定位正确。
- 验收过程中修正了脚本的移动隐藏状态栏等待、异步分页等待与隔离API切换方式；前次失败证据以 `failure-mobile-selector.*`、`failure-page-wait.*`、`failure-fixture-routing.*`、`failure-search-wait.*` 保留。这些是验收脚本问题，不计为产品视觉修复。产品中发现并修复的功能边界是跨页来源选择、空搜索详情、重复上传和任务反馈定位，最终交互证据均通过。
- 本轮无未解决P0/P1/P2视觉问题；保留此前P3：没有原字体文件、业务内容与行高不同，不能宣称严格像素等同。未重新执行旧高级工具全部流程，本轮聚焦受影响的新工作台与任务链路。


## 六行业与消费品专属子库调整（2026-11-02）

final result: passed

本轮按用户图一文字确认的六行业修改图二行业卡片，新增离散制造/流程制造层级；用户追加明确化妆品必须在消费品制造下面。页面主卡片6个，离散4、流程2；化妆品子库在消费品制造内部，1105份资料显示真实统计。总览卡片保留现有字体、蓝白色板、圆角与间距，层级与文案属于用户授权的功能调整。

- 源图：`data/runtime/industry-images-20261102-v1/reference-images/2.png`；用户后续截图说明原来在“历史空间”中的化妆品归属需要修正。
- 实现与实际浏览器证据：`taxonomy-qa/industry-spaces-desktop.png`、`industry-spaces-mobile.png`、`result.json`，均相对上述采集目录。桌面CSS viewport1280×850、deviceScaleFactor1，使用行业区域元素截图；移动390×844。新层级与旧源图状态不同，不声称逐像素一致；这一轮按明确分类与可用性验证。
- 通过实际Playwright检查6主类、4/2分组、化妆品仅在消费品内、子库1105统计、点击专属子库准确进入该范围、移动无外层横向溢出；pageerror为空。新分类脚本先于后端加载导致的短暂空组已在后端重启后消除，最终截图6主类均可见。
- 本地图文素材浏览器 `图片浏览.html` 单独通过50张分页、标签筛选、介绍搜索、本地图片加载和390px布局检查，记录 `taxonomy-qa/gallery-result.json`。浏览器只读取本地文件，未替用户访问或登录图片源站。
- 本轮没有未解决P0/P1/P2分类显示或交互问题。谷歌自动采集和素材审核另按 `进度.json` 记录；视觉验收不表示3750张已经采齐或行业内容准确。

## 前端恢复与全量图文工作台（2026-11-02）

final result: passed

本轮保留已选参考的蓝白色板、侧栏、圆角卡片、字号和主入口，修复用户截图里的500后空白知识空间，并增加授权的图文画廊和检索证据展示。用户已允许本机Playwright；全程新建临时Chromium，只访问localhost，未使用Chrome个人登录资料。

证据目录：`data/runtime/platform-completion/1ba0cc0f828842a690202be2931eba52/ui-complete/`。

- `initial-offline.png`：浏览器内拦截summary为500，仍显示六行业、未知数量和可操作重试。解除拦截后10秒内自动加载真实统计；再断连保留上次快照并标记过期，恢复后错误提示清除。
- `dashboard-desktop.png`：1440×1000 CSS viewport，完整页截图；6主行业及4/2层级齐全，化妆品位于消费品内，4086图片、4094文档、7686Chunk为真实后端统计。动态31图包含1006帧。此为已授权的新数据和功能布局，不宣称逐像素等同早期参考11文档的页面。
- `documents-desktop.png`、`documents-mobile.png`：默认50条图文画廊，原介绍/搜索分类/OCR/文本和视觉向量状态可区分；桌面筛选对齐，390px筛选和搜索宽度可用，图片保持比例，不遮盖操作；表格入口保留。
- `similar-images.png`、`chunk-evidence.png`：同库相似图可打开，原图与来源证据可加载；视觉相似得分不表述为工艺正确概率。
- `retrieval-ocr-context.png`：真实模型选中材料范围，查询“硬质PVC管材 高速混合 低速搅拌”，返回真实工艺图、BM25/Vector/RRF/Rerank分数和同图OCR证据。开启/关闭同图OCR关联有独立开关。标题+机器OCR标记为未审核。
- 390×844检查总览、文档、Chunk、检索、任务五页无外层横向溢出；离线/恢复、消费者父卡片转专属子库、分类SQL过滤、空搜索、50图分页、图像加载及真实检索全通过，pageerror为空，见`result.json`。
- 已实际打开检查桌面总览、检索结果和手机文档截图。仍保留早期参考内容/字体差异及历史失败截图，未通过拉伸图像或删减真实内容伪造逐像素一致。

最终回归：Python137/137、JavaScript14/14、185个Python文件内存编译、Vite保留dist构建均通过。初次最终构建尝试因本终端没有npm而未执行；随后使用已有Node运行相同Vite入口成功，没有修改依赖。相关失败日志保留。交互验收的API故障只在测试浏览器中模拟，没有破坏主库或关闭用户服务。
