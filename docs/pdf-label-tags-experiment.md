# 原生 PDF 标签与轴面实验

实验代码位于 `experiments/pdf_label_tags/`，未接入现有第一、二、三阶段服务。
API 只接收整页预览和带位置的原生文字，输出独立建造视图的标题文字 ID、类型和归一化矩形。
提示词是 `experiments/pdf_label_tags/localize.md`。模型不统计构件，不填写逐根 view_tags。

本地在模型确定的矩形内调用项目规格解析器，以每个原生标签出现位置建立独立记录。
假设每根钢材都有一个规格标签，因此每个标签计一根（组合规格计一个组合，另存组件倍数）。
相同规格、相同文字在不同位置不会被合并；不同视图不去重。多视图区域重叠造成的标签归属冲突单独记录。

每条记录继承标题中的所属面名，去掉 PLAN AT 等标题前缀，保留完整原始项目命名。
程序将原生轴面文字与邻近且同向的长矢量线关联，优先虚线，然后按规格文字方向、距离及线段覆盖范围提出轴面归属。
轴面文字周围的紧凑平行边框会被排除，包括分别绘制的填充边框和描边重复路径。
短参考引线也可作为起点，沿同向、近共线且间隙不超过半个字体大小的原生线段追踪覆盖范围，保留全部支持矢量ID。
不会无限延长参考线；无连接证据的远处构件仍不自动归属该轴面。排除的边框ID及追踪容差写入JSON，便于审计。
距离单位为 PDF point，阈值按字体大小计算，不是实际尺寸毫米。匹配是基于“文字在构件附近”的工程启发式，不表示已证明构件拓扑。
不能定位到具名轴线的轴间次梁/斜撑仅保留所属图纸面；轴间上下左右名字放在 `axis_context`，不会伪装成所属轴面。
多个同向轴面都满足邻近条件时保留歧义，不自动选最近者。

## 运行

在项目根目录 PowerShell 执行（使用 `.env` API 配置；定位默认 high）：

```powershell
.\.venv\Scripts\python.exe tools/test_pdf_label_tags.py --pdf 'C:\path\drawing.pdf' --output tmp/pdf_label_view_tags/my-run
```

每次付费试验使用新输出目录。复用该目录的真实 API 定位回复、仅重新执行本地解析：

```powershell
.\.venv\Scripts\python.exe tools/test_pdf_label_tags.py --pdf 'C:\path\drawing.pdf' --output tmp/pdf_label_view_tags/my-run --reuse-localization
```

复用前校验 PDF、提示词和响应 schema 哈希。不会修改源 PDF、原任务或现有阶段结果。
`--expected-view-count` 和 `--expected-labels-per-view` 是可选验收参数，只在全部识别结束后比较，不进入模型输入，也不补删结果。
没有项目文件名、轴线名、标高或预期数量的识别分支。

输出包括：

- `index.html`：300 DPI 原图、标签编号框、逐标签表格及轴线参考线证据；点击图片切换原始大小。
- `labels.csv`：逐标签台账，含规格、view_tags、匹配状态、PDF 文字 ID 与轴间位置。
- `report.json`：完整原始坐标、方向、标签计数、匹配依据、归属冲突及可选验收结果。
- `regions.json`：模型定位区域；`responses/`：真实 API 请求提示词、schema、响应和签名；`native/`：原生文字与矢量；`logs/`：脱敏日志。

当前限制：只支持可提取文字/矢量的 PDF，以及现有项目规格/轴面命名解析器覆盖的格式。
单行多个规格被保留为独立标签，但共享行级 bbox，标记位置歧义，不进行轴面自动归属。
不计算长度、不识别拼接拓扑、不构建三维模型，也不在跨视图间去重。
图框、详图或说明中若存在规格文字而模型把它裁进区域，也可能产生额外标签，应通过原图验收定位质量。

测试（只有纯本地几何测试，不模拟 API）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_pdf_label_tags.py -v
```
