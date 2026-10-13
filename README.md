# LLM Count v2

从结构钢材 PDF 建立可追溯的逐根台账。保留 FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API 和 openpyxl；PDF 文字和矢量提供定位证据，视觉模型解释构件，程序负责坐标求解、名义长度和去重。

## 环境与启动（Windows / PowerShell）

在本 README 所在目录执行：

```powershell
# 已安装 uv 时跳过第一行；安装后重新打开终端
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# 仅首次配置时复制；已有 .env 不要覆盖
Copy-Item .env.example .env
```

第一阶段不需要 API 配置。第二阶段在 `.env` 填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，或在网页临时填写。模型需支持图片输入以及 `response_format: json_object`。网页提供的 Key 不写入 SQLite、任务文件或导出包，仅在内存中保留至第二阶段结束，重启/第二阶段重试需重新填写或使用 `.env`。

DeepSeek 官方 API 的图纸任务使用 `deepseek-flash`，官方视觉说明：https://api-docs.deepseek.com/guides/vision/ 。项目对该模型使用 `reasoning_effort=low` 与 24000 的输出 token 上限，并在日志记录这些参数。`deepseek-v4-pro` 不在该官方视觉支持列表；旧 Pro 响应保留用于诊断，不应用作视觉验收基准。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8120
```

打开 http://127.0.0.1:8120 。使用单个 Uvicorn 进程，不加 `--workers`；模型任务按锁串行运行。整个应用的实际根目录是本 README 所在目录，不是外层同名目录。

## 三阶段确认

单独测试第二阶段构件识别（读取指定任务第一阶段目录，调用 API，结果保存在 `tmp/member_recognition`，不修改原任务）：

```powershell
.\.venv\Scripts\python.exe tools\test_view_members.py --job-id b46f1e7a0014420892bbf712bc289887 --view-title "PLAN AT EL.107.500(TOS)" --regions tmp\ai_view_localization\report.json
```

更换任务和视图标题即可测试其他图纸；尚未定位的目录需提供对应裁切测试的报告。单元测试不调用 API：` .\.venv\Scripts\python.exe -m unittest discover -s tests -q`。

1. **钢材规格与视图目录**：上传 PDF，程序提取原生文字和矢量后立即输出截面规格种数和完整目录，以及平面、立面、概览的数量和名称，然后暂停。此阶段不调用 API，不识别候选构件、首尾或尺寸关系。每个规格可展开查看原图文字、文件页码和证据 ID；每个平面列出 EL 与 TOS/BOB，每个立面列出原始轴线名称。初始视图区域是整页占位，不表示独立视图已定位；第二阶段再进行定位。
2. **逐视图构件与长度台账（新任务）**：沿用第一阶段确认的视图 ID、名称、规格目录；定位并裁切各视图后，将 300 DPI PNG、该区域文字、规格标签、矢量和目录一同交给 AI。PDF 坐标仅用于关联图片与证据，不要求全局 XYZ。提示词位于 `app/prompts/stage_two_members.md`，按标签定位、宽度与连接切分、拼接拆分、数量复核、尺寸链/对称/勾股长度计算、最终复核的顺序工作。AI 仅返回 spec、view_tags、member_cnt、length_mm、basis、issue 六字段，服务端补充来源和校验信息。网页可逐视图修改保存；长度是模型候选，必须核对依据，程序未证明识别准确性。
3. **去重（新台账尚未接入）**：计划按视图标签集合＋规格＋长度汇合，使用 member_cnt 保留同组多根数量，无基准视图；数量冲突待确认。当前新台账暂停在第二阶段，禁止进入旧坐标去重以免输出零根或错误数量。旧任务的坐标流程仍可读取和运行。

第二阶段输入包保存在任务目录 `member_inputs`，提示词和原始响应在 `responses`，事件日志记录输入规模、标签数/构件段数差异和每行依据。矢量上下文最多 1600 条，完整原始证据保存在 `native`；截取会显示警告。当前视图之外的标签必须有明确轴线/标高关系，只有当前标签时保留待确认。


有问题时修正或勾选“保留待确认项继续”。无法确认坐标、长度或匹配的观察不纳入已确认汇总；覆盖警告保留在导出的 Method Notes。修改上游阶段会使下游失效并隐藏旧下载链接。已确认阶段也可以重新编辑，修改和确认均保留历史。

界面修改需填写依据，保存为带页面位置的人工证据。高级 JSON 编辑可调整完整数据和证据 ID；填写的候选长度、confirmed 标志和最终数量不会被直接采信，而会重新计算。历史中的人工草稿或重试提案可载入后另存为新版本。

第一阶段目录由原生文字证据去重生成，独立于首尾、坐标和候选构件识别是否成功。`data.discovery_inventory` 保存规格、证据、视图目录和数量；模型提出但没有规格文字证据的项目另列为待确认，不增加已提取规格种数。已有 v2 任务读取时也会生成目录，不改变已确认版本或旧导出。当前五份验收图纸应输出 16 种规格、5 个平面、7 个立面和 1 个概览。

后端提示词见 `app/prompts/stage_one.md`，文件名保留，实际用于确认目录后的第二阶段视图定位和构件识别请求；第一阶段不发送模型请求。实际提示随请求记录在日志中。包含 HEA/HEB/IPET/角钢等规格文字、2LF 组合前缀、连接后缀、视图标题线索，以及标高、轴号、尺寸、钢板和螺栓等排除项。提示词变化参与请求哈希，不复用旧提示词的签名响应。

## 坐标与长度口径

第二阶段自动定位采用“AI 提框＋原生证据检查”。模型同时返回结构区域、完整裁切区域、边界理由和证据 ID；程序以独立标题为锚点，将局部轴线、尺寸候选、规格及说明按到结构区域的距离分配，补入完整文字框和附近短矢量。扩边受模型区域附近的窗口限制，防止把页框轴号和右侧引用表纳入。相邻视图标题、部分图签侵入、证据歧义或无效引用转为待确认。人工已确认区域保留，不自动覆盖。

`View.localization` 和 `VIEW_REGION_CHECKED` 日志保存原始框、最终框、扩边证据及问题。自动区域另存 `views/<id>_300dpi.png`，通过视图文件接口读取，并随 ZIP 导出。AI 的裁切自评只是复核意见，不能代替原生证据覆盖检查；真实测试曾出现可见完整构件被错误报告为截断的情况。

- 保存原始轴名、偏移和标高，同时求解毫米坐标。X/Y/Z 是项目全局方向，不是页面像素方向；每个平面/立面定义两个显示方向和一个固定方向，镜像/旋转不改变语义轴名。
- X/Y 轴线需要一个有证据的坐标锚点；尺寸链的 `distance_mm` 是沿全局正方向的有符号差。端点使用 `axis_label` 或 `value_mm`，两者不能同时填写；偏移可用 `towards` 指明朝向哪根轴线。
- Z 使用 EL 值和单位，相对于 `z_origin_m`。保留 TOS/BOB，只有有证据的 `offset_mm` 才能转换为其他基准。低于 TOS 100 mm 的水平斜撑必须应用偏移。缺少转换不能假设为中心线。
- 长度来自有证据的端点差，二维斜撑使用勾股关系，计算式写入审计。输出是图示定位参考线的名义长度，不是含端部切割、间隙扣减的下料长度；也不根据预拱注释推算曲线展开长度。
- 两个观察的端点匹配使用不超过 1 mm 的欧氏距离舍入容差；不会通过连续近似匹配扩大容差，不按规格/长度分组去重。
- 相交、同轴和共线本身不能证明首尾边界。明确端部、拼接或规格变化才形成可确认分段；连续穿过一个轴线的柱不自动按楼层拆分。
- `2LF`/`2L` 先作为组合实体去重，再展开带不同组件编号的单根角钢。最终汇总仅显示单根数量，组合关系保留在 Member Register。

首版只支持有文字和矢量的 PDF、横平竖直构件和二维斜撑。扫描件及三维斜撑保留为不支持/待确认。密集视图的模型上下文以紧凑格式最多提供 1600 条较长矢量原语；完整精度和原语仍写入 native，截取会产生覆盖警告。模型识别是否完整需要逐阶段核对，不能以原语数或模型返回数量作为人工基准。

## API 与数据版本

`POST /api/jobs` 接收多文件 multipart 上传以及可选的 `base_url/api_key/model`，创建 `schema_version=2` 任务。`GET /api/jobs` 提供最近任务，`GET /api/jobs/{id}` 提供状态。

| 接口 | 行为 |
| --- | --- |
| `GET /api/jobs/{id}/stages/{stage}` | 草稿、revision、状态、修改历史和历史快照 |
| `PATCH /api/jobs/{id}/stages/{stage}` | `{revision, data, reason}`，校验并保存完整草稿，返回新版本 |
| `POST /api/jobs/{id}/stages/{stage}/approve` | `{revision, acknowledge_pending, settings?: {base_url,api_key,model}}`，确认并启动下阶段；第一阶段确认可携带第二阶段 API 设置，第三阶段启动导出 |
| `POST /api/jobs/{id}/stages/{stage}/retry` | `{revision, settings?: {base_url,api_key,model}}`，显式重试当前阶段或中断的导出 |
| `GET /api/jobs/{id}/pages` | 原始页面预览与标题候选 |
| `GET /api/jobs/{id}/evidence?page_id=...&view_id=...&q=...` | 按文字/ID 查找定位证据，默认最多 200 条 |
| `GET /api/jobs/{id}/views/{filename}` | 页面或视图 JPEG |
| `GET /api/jobs/{id}/download` | 已确认的 Excel |
| `GET /api/jobs/{id}/views-download` | 已确认的截图/坐标/证据 ZIP |
| `GET /api/jobs/{id}/logs?after=0&limit=200` | 分页读取可解释审计事件（JSONL 游标） |
| `GET /api/jobs/{id}/logs-download` | 下载完整脱敏任务日志，执行中也可使用 |

旧 revision 返回 409；无效坐标/证据或未明确允许的待确认项返回 422。重复确认不会重复创建阶段或导出。任务执行中不能修改草稿。模型给出的描述性视图/观察 ID 在校验前被替换为服务端 ID，不能用作文件名。

SQLite 位于 `data/jobs_v2.sqlite3`，存储任务、阶段、版本、历史和事件；重启后将排队/执行中的工作标记为 interrupted，需手动重试。第一阶段重试只重新提取并生成目录，不发送 API 请求。失败/中断的第二阶段重试会重新校验并复用已保存的匹配响应，只请求尚未完成的识别；新请求以模型、地址、提示词、契约、PDF 内容和裁切区域的哈希签名匹配。旧响应无签名时仅在上传文件未修改时恢复。API 重试可传 `reuse_saved:false` 强制重新请求；等待人工确认时的普通重试仍重新识别。重试人工编辑过的草稿时，新模型结果保存为历史提案，人工草稿不被覆盖。旧版第一阶段已有观察的任务保留原数据，第二阶段可继续程序计算，不自动重跑 AI。

`data/jobs/<id>/` 保存 uploads、native、views、responses 和 output。ZIP 包含三个阶段的 JSON、原始文字/矢量、图像、原始响应和人工修改历史。Excel 包含数量长度汇总、逐根台账、观察台账、长度计算、匹配、异常、历史和覆盖说明。

## 可解释日志与版本管理

网页的“处理依据与 API 日志”提供请求耗时、响应校验、ID 规范化、证据修复、长度计算式、坐标基准、匹配理由和排除原因。完整事件写入任务目录的 `logs/events.jsonl`；实际请求的提示词与契约写入对应 `.prompt.txt`，原始响应与请求哈希分别写入 `.json` 和 `.request.json`。这些文件随最终 ZIP 导出。

模型的 ID 在领域校验前由服务端替换。模型缩短的矢量 ID 只有在唯一对应一个原始线段时才补全；无法唯一对应的证据和越界首尾转为待确认，并保留原始响应。`discovery_checkpoint.json` 记录已识别视图的进行中检查点，不代表确认数量。

缺失端部证据的观察会在第二阶段额外复核，要求逐观察返回首尾类型、原始证据 ID 与具体理由；原识别和复核响应分别保存，日志记录接受或拒绝的原因。平面中的柱截面不能证明柱的纵向首尾。复核不会修改数量、规格或坐标，也不以规格文字单独证明端部。

轴线全名与短名保留对应关系。模型同时返回轴线与等值毫米坐标时，仅消除等值冗余；不一致值仍阻止确认。直接坐标只有在匹配已标定轴线，或关联的尺寸证据支持偏移时才能改写为语义坐标。过程保留修改前后值和证据，写入日志、JSON 和 Length Audit，不能拿视图其他位置碰巧相同的数字补充偏移依据。

第二阶段暂停的任务可单独补做端部复核（调用 `.env` 中的 API，保存提案后以阶段 revision 校验更新草稿；旧版第一阶段已有观察的任务需加 `--stage 1`，新目录阶段禁止端部复核）：

```powershell
.\.venv\Scripts\python.exe tools\review_task_boundaries.py --job-id <任务ID>
```

加 `--repair-empty-views` 时只恢复没有观察的平面/立面，再复核其端部；可加 `--view-id` 限定一个视图。原始提示词、截断响应和恢复响应分别保留。DeepSeek Flash 输出出现 `finish_reason=length` 时，程序保留 `.truncated.json` 并仅重试该请求一次，关闭 thinking 为 JSON 留出输出额度；仍截断则失败，不采纳部分结果。

API 地址、Key、认证头不作为日志字段保存；已知密钥、Bearer 值和 URL 中的认证参数在日志及错误堆栈中脱敏。长请求每 30 秒记录等待事件，结束后记录耗时、token usage、finish_reason 和响应文件，以区分 API 等待、返回空内容和结构校验失败。

项目已建立本地 Git 仓库。`.gitignore` 排除 `.env` 与其变体、虚拟环境、缓存、任务目录、SQLite、日志、PDF、Excel 和 ZIP；仅空白 `.env.example` 跟踪入库。没有向远端上传代码或任务数据。

使用真实图纸做程序验收时，阶段确认可携带 `reason`，例如“流程验证，允许待确认项继续；不是工程确认”。完成状态只表示程序完成三阶段及导出，不代表待确认构件已得到工程认可。

对已经执行中的真实任务进行三阶段程序验收（会自动确认阶段，明确保留疑点）：

```powershell
.\.venv\Scripts\python.exe tools\verify_task.py --job-id <任务ID> --allow-pending
```

该工具将确认记录标记为 `[FLOW_VERIFICATION]`，并写入任务目录的 `flow_verification.json`，区分程序跑通与工程认可。没有 `--allow-pending` 时，在第一处人工确认门停止。

旧任务文件保留，不转换成 v2；旧 Excel 不能自动成为新坐标台账。`llm_runner.py` 和旧规则测试仅保留历史校验能力，Web 任务入口使用新的 pipeline。

## 验证

PDF 渲染测试使用第二张样本图纸中人工目视核对的两个平面区域（见 `tests/fixtures/plan_02_views.json`），验证标题、轴线、拼接和说明覆盖，排除相邻视图标题与图签，并检查 300 DPI PNG 尺寸。测试不调用 API，也不代表自动视图定位已通过验收。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_pdf_rendering.py -v
.\.venv\Scripts\python.exe tools\render_sample_views.py
```

使用已有 `.env` API 对第二张样本进行真实自动定位验收（会调用 API）：

```powershell
.\.venv\Scripts\python.exe tools\test_ai_view_localization.py --file "C:\路径\4422-AI-DU-302003PR01SA-02_00.pdf" --reference tests\fixtures\plan_02_views.json --output tmp\ai_view_localization
```

输出包括原始 AI 框、补边后区域、300 DPI 图片、模型响应、日志和报告。`--file` 可指定任意含原生文字与矢量的 PDF，`--page` 指定页码（默认 1）；默认目录按 PDF 文件名和页码隔离，位于 `tmp/ai_view_localization/<文件名>/page_001/`，可用 `--output` 指定其他目录。不限制视图种类和数量。

`--reference` 是可选人工参考 JSON，需要 `file`、`page` 和 `views: [{title,bbox}]`，文件名和页码必须与输入一致；参考框不发送给 AI。无参考时报告明确标注 `reference_provided:false`，各视图 `reference_passed:null`，`passed` 仅表示程序边界检查通过，不能证明完整性。有参考时还校验参考标注覆盖和视图集合，不再固定要求两个视图。`ai_review_all_passed` 单独记录 AI 自评。加 `--reuse-proposals` 可在原生数据未改变时复用原始定位响应，再执行新的裁切复核；不要反复调用直到模型自评“通过”。

测试其他 PDF 示例：

```powershell
.\.venv\Scripts\python.exe tools\test_ai_view_localization.py --file "C:\图纸\other.pdf" --page 1
```

只让 AI 定位并导出高清图片、由自己查看，不调用 AI 检测生成图片时，加 `--skip-crop-review`。报告中 `crop_review_skipped:true`、`ai_review_all_passed:null`，程序仍记录原生边界核验问题；不要将导出完成当作人工验收通过。模型返回非法 JSON 或多余字段时，原响应保留，工具最多重试一次格式错误。

第二个命令将两个高清 PNG、2600 像素预览及报告写入 `tmp/pdf_render_test/`（Git 忽略）。`render_png(entry, dpi=300)` 直接从 PDF 矢量渲染，无损输出且不受预览尺寸限制；现有 AI 输入和网页预览仍使用 JPEG，新增测试不会自动提高所有 API 请求的图片尺寸。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

回归测试使用模拟模型响应，不调用真实 API。覆盖坐标链、单位/偏移、TOS/BOB、拼接、首尾反转、重复投影、组合角钢、相邻/重叠冲突、版本校验、重启、重试和 Excel/ZIP 导出。若图纸在其他目录：

```powershell
$env:LLM_SAMPLE_DIR = '实际样本 PDF 所在目录'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\inspect_samples.py --directory $env:LLM_SAMPLE_DIR
```

样本检查应发现 5 个平面（含基础平面）、7 个立面和 1 个概览，登记立面独有的 EL.103.800 和 EL.111.500。样本检查只是验证原始 PDF 可提取及视图覆盖，不能证明模型构件识别准确。

`tools/inspect_samples.py` 输出 `tmp/sample_review/report.json` 与代表性构件人工核对模板。模板在填入经人工确认的首尾、分段、长度及重复关系之前不是黄金基准。真实 API 全图统计应在界面核对后建立验收基准，不能沿用旧模型结果。
