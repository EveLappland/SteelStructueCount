# LLM Count

原生标签与轴面特性交付：见 [独立工具说明](docs/pdf-label-tags-experiment.md)。输出 `stage_one_facts.json` 供标签约束的第二阶段使用，现有任务不自动迁移。

固定标签第二阶段：见 [尺寸识别说明](docs/label-constrained-lengths.md)，使用 `tools/test_label_lengths.py` 运行网格子代理、总代理和按需专家/仲裁。

从钢结构 PDF 建造布置图建立规格、名义长度和数量台账。FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API、SQLite 和 openpyxl。默认自动执行三个阶段并导出，也可逐阶段暂停。

## 当前流程

1. `app/stage_one.py`：Agent 按页判断用途，只对包含平面/立面建造布置的页面定位视图、枚举面名称和钢材种类。概览、节点、底板和说明等保留跳过理由。有效视图生成 300 DPI PNG；再结合该视图高清图与原生文字，独立统计总数量和各规格数量，保存 `quantity_reference`，供第二阶段比较。每视图独立统计，2LF 等先计一个组合；未知数量不当作零。数量标准与其图片绑定，修改裁切后失效。
2. `app/grid_workflow.py`：将每张第一阶段高清图自动划分为重叠网格，网格子代理逐标签识别，再由总代理结合完整原图、高清网格和全部观察输出逐根记录。长度专家随后核验 UNKNOWN、分段未解决记录及所有斜撑，读取图示尺寸、共享拼接基准和直角边表达式，由本地计算。所有子请求、总代理、长度专家与重试共用一个请求线程池，无并发叠乘。标题、当前所属面、轴面目录、规格与数量标准均来自第一阶段；程序补入来源面。总代理数量或引用矛盾最多反馈两轮，格式失败只重试当前请求（最多两次）；不强制凑数，不直接增删构件。此前严格的重叠/规格合并规则保持不变。
3. `app/group_reconciliation.py`：程序按完整 `(spec, view_tags, length_mm)` key 去重，不调用 AI 重写所属面、规格或长度。先累加同一来源中同 key 的 `member_cnt`，再比较跨来源数量：一致时只计一组，不一致时全部独立保留并记录冲突。不同 key 不合并。目录外规格、缺失长度和未完成来源保留待处理；最后展开组合角钢并导出。

各视图地位相同。不同标高平面补入不同所属面后 key 不同，不能因为平面内位置、规格和长度相同而去重。程序检查通过不等于工程准确性验收。

## 环境与启动（PowerShell）

在本 README 所在目录执行（不是外层同名目录）：

```powershell
# 已安装 uv 可跳过第一行
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# 仅首次配置，已有 .env 不要覆盖
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8120
```

`.env` 配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，可设置 `LLM_MAX_CONCURRENCY=5`。模型须支持图片和 JSON。网页可覆盖配置，API Key 只放在内存。默认思考强度 high，手动重试可选 low / high / max。

打开 http://127.0.0.1:8120 。同一任务数据目录只运行一个 Uvicorn 进程。阶段之间串行；第一阶段页面判断/定位、逐视图独立数量统计分别受请求上限限制，PDF 渲染串行。第二阶段无论同时处理多少视图与网格，所有 AI 请求合计不超过 `LLM_MAX_CONCURRENCY` 或网页覆盖值。更新代码后等待任务结束再重启服务，已有结果不会自动重算。

新建任务可勾选“仅测试第一、二阶段，完成后暂停”，对应上传参数 `stop_after_stage=2`；自动确认第一阶段，第二阶段完成后停在待确认状态，不创建第三阶段或导出。若同时勾选逐阶段暂停，则仍先停在第一阶段。

## 接口、数据和追溯

- `POST /api/jobs` 上传 PDF，默认 `auto_run=true`、`stop_after_stage=4`；false 则逐阶段暂停。`stop_after_stage=2` 只自动运行前两阶段。
- `GET /api/jobs/{id}` 查询状态、版本和下载链接。
- `GET/PATCH /api/jobs/{id}/stages/{stage}` 读取/修改草稿。
- `POST /api/jobs/{id}/stages/{stage}/approve`、`retry` 确认/重试，携带 `revision` 防旧版本覆盖。`reuse_saved=true` 仅复用请求指纹一致、重新校验通过的响应。
- `/pages`、`/views/{filename}` 页面目录、按需预览和冻结高清图。
- `/logs`、`/logs-download`、`/download`、`/views-download` 审计、Excel 和证据包。

SQLite 保存版本、草稿、修改理由和状态；`data/jobs/{id}` 保存 PDF、图片、模型响应和日志。上游修改使下游失效；重试不覆盖人工草稿。结果组 ID 格式修复保留映射，原响应及观察引用不改写。

任务全部完成后，可切换到第二阶段，点击“从第二阶段重新执行”。保留第一阶段视图、目录和高清裁图，重新调用 AI 识别；旧第二阶段台账（含人工修改）存入历史，本次采用新结果，第三阶段和下载链接失效后重新生成。自动任务继续执行到导出，逐阶段暂停任务在第二阶段完成后等待确认。此操作不复用旧识图响应，可在按钮旁选择思考强度。

Excel 包含汇总、单根台账、组分配、原始观察、待确认项、用途说明和历史。ZIP 包含截图、原生证据、响应和审计 JSON。旧坐标和旧定位流程已停用，历史文件和记录保留，可查看完整 JSON、下载已有结果。清理依据见 [docs/refactor-audit.md](docs/refactor-audit.md)。

## 提示词

当前执行使用 `app/prompts/` 中的 `page_purpose.md`（页面用途）、`construction_catalog.md`（定位与目录）、`view_quantity.md`（第一阶段独立数量标准）、`grid_child.md`（网格观察）、`grid_master.md`（总代理逐根汇总）、`grid_repair.md`（本地矛盾反馈）、`length_expert.md`（长度与分段专家）、`length_evidence.md`（补证调度）、`length_verifier.md`（独立核验）、`length_arbiter.md`（自动仲裁）。`stage_two_members.md` 保留原有单视图提示词，不是本分支的网格请求入口；`stage_three_groups.md` 仅用于历史 AI 提案工具。

第二阶段 API 的 `view_tags` 只返回所在轴线/标高面，允许空数组。接收后程序先补入第一阶段图纸标题所属面、规范化名称、排序去重标签，再记录 `source_plane_tag` 和 `member_key`。网格重复观察通过 `candidate_ids` 与 `decisions` 显式关联为逐根记录；不做跨视图去重。总代理每行 `member_cnt=1`，程序在长度目录中求和。第一阶段标准比较的是 member_cnt 合计而非 JSON 行数；总数和各规格差异分别保留。第三阶段只比较完整 key，不将不同标签集合做并集，也不修改规格和长度来凑数。2LF / 2F / 2L 先计组合，第三阶段最后展开一次。

每次网格运行保留 `grids/` 裁切清单、`grid_runs/attempt-grid-*/<view_id>/` 第一阶段输入包、局部观察、定位映射、各轮总代理原始正文及检查结果。`responses/` 保存实际模型响应、完整 Prompt、schema 与请求指纹；日志记录请求阶段、视图、图片指纹和请求并发。共享台账和 checkpoint 只由协调线程按视图目录顺序写入，失败视图不影响其他视图。

达到反馈轮次上限后，格式有效的候选台账完整保留，结构/来源矛盾附在 `grid_checks` 与 `remaining_checks`，显示“完成（有待核对项）”；不因未解决检查清空整张台账。缺失子网格或无法解析的回复明确标记该视图失败，不用不完整数据伪装完成。

### 长度与分段专家

`app/length_expert.py` 管理尺寸引用、原图归一化端点、表达式与本地合入；`app/length_context.py` 生成工作包和无损扩展裁图。专家按视图核验全部构件，包括已有数值的DIRECT、UNKNOWN、UNRESOLVED和DIAGONAL；已填长度不能绕过核验。一次输出该视图的共享尺寸引用及每个目标的 DIRECT 或 HORIZONTAL/VERTICAL 表达式。标高引用先从米换为毫米；本地按引用加减乘，再计算勾股并保留两位小数。OF SPLICE/标尺只在一处出现时，须说明为什么可用于远处构件；不能只凭规格相同或近似对称套用。

每视图最多两轮：首轮之后只有明确补图请求或本地引用/格式关系错误才追加；补图最多一次，包含目标端部、拼接和远处参考尺寸。普通 UNKNOWN 不触发无限重算。两个角色的语义判断仍可能错误，表达式通过只说明本地引用与算术一致，不声明图形对应已验证。

比例标定使用图示尺寸及其原图点位，各方向至少两个不同跨度/位置，比例差不超过 2% 才用于辅助测量；标高数值和重复抄写同一跨度不作独立标定。点位由模型给出，误差仍可能很大。`length_estimate` 与确定长度分开存储，不参与名义长度 key 或跨视图去重；比例文字不直接生成确定长度。估测误差说明使用假设端点各 ±3 像素的示意范围，不是统计置信区间。

专家不修改规格、member_cnt、所属面或标签来源。疑似漏拆/误合并返回 CONFLICT 和说明，本版不自动改变分段行数；专家长度先保存为候选，由自动仲裁按原ID局部合入。API失败、缺引用或未解决时保留原台账。`length_review` 保存原长度/问题、专家依据和本地检查，网页可展开查看；后续正常导出另有 `Length Expert Audit` 工作表。

### 自动补证、核验与仲裁

`app/length_arbitration.py` 在长度专家后调度三个角色，全部复用 `grid_workflow.py` 的请求队列，和网格/总代理共享 `LLM_MAX_CONCURRENCY`（`.env` 可配置，未设置默认 5，网页显式设置优先）。本地裁图不计API请求并发。

全部构件都进入长度核验，避免原识图漏看拼接却填入了貌似合理的DIRECT值而绕过复核。专家只提出候选，不直接覆盖台账。证据调度代理按目标端点、拼接标记和完整尺寸链请求合并高清图，也可请求已上传的其他视图；服务器只接受目录内 view_id，不接受模型提供的文件路径。独立核验代理看完整图与补图，首轮不接收旧长度/端点/专家候选。仲裁代理比较 master、专家及独立核验的证据，可选原候选或重新推导；所有加减乘和勾股仍由本地计算。

补证/语义修订预算最多两轮，另有有界传输/格式重试。不等待人工，不无限循环。结果标记 SUPPORTED（图示依据支持）、INFERRED（自动选择最佳可用候选）或 UNRESOLVED（无有效长度）；失败降级也保留每根构件与完整标签。推断不是工程确认，不能以消除 UNKNOWN 代替准确率。此版本不增删分段行、不因数量标准强行凑数；跨视图补图用于取证，不直接认定重复。

`grid_runs/.../<view_id>/arbitration/` 保存各角色输入/原始回答/本地检查、补图清单、所有候选及最终决定。每行 `length_arbitration` 保存原值、最终值与裁决依据；网页显示仲裁状态，最终Excel增加 `Length Arbitration` 审计表。原始任务与历史结果不自动改写。人工参考答案仅由离线评分工具在所有API调用结束后读取，不加入工作包或Prompt。

只测试长度代理链（专家、补证、独立核验、仲裁），冻结已有真实 master 输出（仍使用生产共享调度队列，调用付费 API，不修改源任务）：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe tools/serve_stage12.py --root tmp/length_expert_feature_server --port 8123
# 另一窗口执行；--fresh 则重新调用 children/master，不冻结旧结果
.\.venv\Scripts\python.exe tools/run_length_expert_test.py --source-root tmp/stage12_feature_server --source-job <源任务ID> --root tmp/length_expert_feature_server --max-concurrency 5
```

源 master 及其图片指纹经过验证后复制到新任务，原始回答不是人工构造的模拟响应。`acceptance/` 保存前后结果、真实调用/并发、数量身份不变检查和耗时。可选 `--baseline <JSON>`，格式为 `[{"spec":"HEA260","length_mm":6000,"member_cnt":2}]`；所有推理结束后才读取并评分，不发给 API。相同规格与两位小数长度按多重集合比较，不代表实体位置已核对。

## 测试和真实验收

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
$env:PYTHONPATH = "$PWD;$PWD\tests"
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/*.cjs
```

单元测试不调用付费 API，覆盖渲染、长度、并发、来源标签、版本控制、恢复、失败重试和数量守恒。

本分支的隔离第一/二阶段真实联调（允许调用当前 `.env` 的付费 API，不修改现有任务）：

```powershell
# 窗口一：独立数据目录，避免与现有服务共享数据库恢复状态
.\.venv\Scripts\python.exe tools/serve_stage12.py --root tmp/stage12_feature_server --port 8122
# 窗口二：可重复传入 --pdf，支持任意文件名
.\.venv\Scripts\python.exe tools/run_stage12_test.py --pdf "C:\path\drawing.pdf" --base http://127.0.0.1:8122 --max-concurrency 5 --output tmp/stage12_test
```

真实联调经公开上传接口运行，只到第二阶段；报告检查数量标准交付、网格模式、实际 HTTP 并发峰值、来源面补入及第三阶段未执行。它不读取人工正确答案，不改写模型输出，也不宣称准确率。阶段结果、长度专家记录和逐视图数量差异保存在输出目录。

完整真实验收（新建任务，复制源任务的全部 PDF，不修改源任务，会调用付费 API）：

```powershell
.\.venv\Scripts\python.exe tools/run_full_test.py --source-job <源任务ID> --baseline "<MTO基准.xlsx>"
```

使用用户提供的原版 `tools/judge.py`，逐根比较规格和长度，默认 ±1 mm、排除 PL 钢板。全部模型调用和导出结束后才读取 MTO；结果保存在新任务 `acceptance/`：`judge.txt` 原始评分、`diff.csv` 逐根差异、`score.json` 和 `run.json`（文件指纹、版本、耗时、请求数）。源任务已有导出时另存相同口径的旧结果评分。不根据评分回写台账。

只评分已有 Excel：

```powershell
.\.venv\Scripts\python.exe tools/judge.py --baseline "<MTO基准.xlsx>" --candidate "<steel_takeoff.xlsx>" --csv diff.csv
```

评分返回 1 表示存在差异，不表示程序崩溃。可选独立工具：`render_views.py` 渲染、`benchmark_view_concurrency.py` 并发、`test_group_dedup.py` / `test_group_reconciliation.py` 分组诊断、`experiment_label_colors.py` 着色对比、`score_reference.py` 完整所属面的 JSON 参考核验。

# 长度复核费用控制

第二阶段默认使用 `LENGTH_REVIEW_MODE=economy`：复用 PDF 文字和已识别台账，一次核验，仅冲突或缺少依据的记录再仲裁。`.env` 中 `LENGTH_REVIEW_MAX_CALLS=2` 限制每视图长度复核请求次数，包含格式及传输失败的重试；网格识图、总代理及第一阶段调用另计。`full` 可恢复原多角色流程，但同样遵守设定的调用上限。修改后需重启服务。

尺寸引用增加原文 `source_ids`、尺寸基准 `baseline_basis`；端点增加 `endpoint_kinds`。程序核对来源及数值，几何对应仍为模型判断；未取得充分依据的记录保留推断或未知状态。事实目录和完整中间记录随证据 ZIP 导出。详见 [长度仲裁设计](docs/automatic-length-arbitration.md)。

## 三阶段自动全量测试

`uv run python tools/run_pipeline_test.py --base http://127.0.0.1:8124 --pdf <文件1.pdf> --pdf <文件2.pdf> --output tmp/full_test`

脚本通过公开接口上传 PDF，自动运行到第三阶段与 Excel/证据包导出，保存各阶段 JSON、请求次数、token 用量、耗时与数量去向检查，不读取参考答案或修改构件。监控断开后用 `--resume-job <任务ID> --output <原输出目录>` 继续，不重新上传或重复付费识图。长度复核预算由服务进程的 `.env` 配置决定；启动测试脚本前应确认服务已加载相同配置。既有 `tools/run_full_test.py` 的 MTO 评分入口保持不变。

若仅修复本地裁决落账规则，可用 `tools/replay_length_finalization.py --source-root <源服务根目录> --source-job <已完成任务ID> --root <服务根目录>`，统一重放所有视图保存的真实裁决，生成新任务后重新去重导出，不新增模型调用。脚本校验图片指纹及构件身份，保留原任务，不接收人工候选或参考答案。`evidence_ids` 允许真实 PDF 文字与实际发送图像共同引用；文字不能替代图像，未知 ID 仍拒绝。

独立运行 `tools/judge.py` 评分另需 `uv pip install "pandas>=2,<4"`；评分在全部推理结束后执行，不向后端发送基准。
