# 第一阶段：原生标签、轴面与高清裁切

本模块已接入 8120 主网页新任务。目录 `experiments/pdf_label_tags/` 和本文文件名保留早期命名，不能据此认为它仍是独立实验。

入口：[native_web.py](../app/native_web.py) → [run.py:run](../experiments/pdf_label_tags/run.py)。输出契约由 [label_facts.py](../app/label_facts.py) 生成。

## 分工与处理顺序

1. 程序提取每页原生文字、位置和矢量，生成 100 DPI 整页定位预览。
2. AI 根据预览与带 ID/坐标的文字目录，找出实际建造 PLAN/ELEVATION。跳过图框、索引、说明、概览和节点详图等非目标内容；无目标页可返回空列表。
3. 程序检查返回矩形范围、标题 ID、重复标题及标题是否位于区域内；不静默修正无效定位。
4. 在各区域内解析钢材标签、轴线或面名称、方向和邻近关系；以各标签出现位置建立独立记录。
5. 从 PDF 按区域渲染 300 DPI 无损 PNG，交付带指纹的标签事实包。

当前主链没有额外独立的“页面用途 Agent”步骤：非建造内容由定位 Prompt 排除。旧链的用途过滤不应写成这里的功能。无目标区域的页不会产生供第二阶段使用的视图高清裁片，仍有整页定位预览和定位记录。

定位 Prompt：[localize.md](../experiments/pdf_label_tags/localize.md)。回复只包含视图标题 ID、类型和归一化区域：

```json
{"views":[{"title_id":"原生标题文字ID","kind":"PLAN","bbox":[0.1,0.1,0.8,0.9]}]}
```

此处坐标与 ID 仅示意。坐标原点为页左上角，范围 0–1。模型在本阶段不填写钢材数量、长度或逐根所属面；这些标签信息由程序解析。

## 标签、数量与所属面

- 输入假设是每根构件/组合都有一个规格标签。每个标签 `member_cnt=1`；组合规格另存 `component_multiplier`，此阶段不展开。
- 同规格文字在不同位置是不同标签，不因内容相同合并；跨视图也不去重。
- 当前标题所属面必加入 `view_tags`。面名去除标题前缀，保留完整原始命名与 TOS/BOB 等标高基准，不缩成单个轴号。
- 程序将轴面文字关联到附近同向参考线，结合标签方向、距离和线段覆盖提出归属；排除紧凑气泡边框，保留支持证据。
- 线段追踪按字体尺度容差执行，不无限延长轴线，也不按当前样本文件名或某组固定轴名匹配。
- 无法确认附加面时，只保留当前图纸面；轴间前后/上下关系写入 `axis_context`，不假装构件恰好位于某个具名面。
- 多个可能归属、区域重叠或多规格共用文字行 bbox 等情况保留歧义。

`AXIS_PROPOSED` 表示程序邻近关系提议，不是物理拓扑证明。第一阶段提供的固定身份与原生文字可以约束后续识别，但标签数量和归属并非无条件正确的工程真值。

## 交付结构

`stage_one_facts.json` 格式为 `native-label-facts/v1`。主要内容：

| 层级 | 字段与用途 |
| --- | --- |
| 包 | `source_sha256, bundle_sha256, count_unit, coordinates, limits, views` |
| 视图 | `view_id, page_id, title, kind, plane_name, bbox_page` |
| 图像 | `image_file, image_sha256, image_size_px` |
| 轴面 | `axes, axis_order`，保存完整名称、方向、位置与证据 |
| 标签 | `label_id, text_id, spec, member_cnt, component_multiplier, bbox_view, direction, view_tags, tag_status, axis_context, axis_evidence` |
| 原生文字 | `text_facts` 中的 ID、原文、位置与可解析的 `values_mm` |

图上位置用于标签/证据/图片对应，不是构件的毫米空间坐标。原生尺寸与标高数值按规则转为毫米，后续模型引用文字 ID。

同时输出 `regions.json`、`report.json`、`labels.csv`、`index.html`、`native/`、`responses/`、`logs/` 和高清图。事实包与图片在第二阶段调用前重新校验指纹；第一阶段不计算长度或拼接拓扑。

## 并发与独立运行

网页按 `LLM_TASK_CONCURRENCY` 并发文件，默认 10；单文件逐页处理。PyMuPDF 原生操作加锁，API 等待可以并发。所有文件交付后进入第二阶段。

在项目根目录运行以下命令会**直接调用配置的 API**：

```powershell
.\.venv\Scripts\python.exe tools/test_pdf_label_tags.py --pdf 'C:\path\drawing.pdf' --output tmp/pdf_label_view_tags/my-run
```

仅重新执行本地解析，复用相同 PDF/Prompt/schema 的定位记录：

```powershell
.\.venv\Scripts\python.exe tools/test_pdf_label_tags.py --pdf 'C:\path\drawing.pdf' --output tmp/pdf_label_view_tags/my-run --reuse-localization
```

没有 `--api` 开关。新付费实验使用新输出目录。`--expected-view-count`、`--expected-labels-per-view` 仅在完成后比较，不进入 Prompt、不补删输出；实际使用应以自己的独立验收为准。

## 限制与验证

可提取文字/矢量、规格与轴面命名格式在解析器覆盖内时适用。定位把说明或详图裁进区域，可能产生额外标签；标签缺失、字体编码异常也会传递到后续阶段。不会从图片自动修补原生标签缺失。

本地几何、引用与归属约束测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_pdf_label_tags.py -v
```

后续交付见 [第二阶段](label-constrained-lengths.md)，整体指标口径见 [测试与指标](validation.md)。
