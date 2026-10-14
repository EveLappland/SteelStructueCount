# LLM Count v2

从结构钢材 PDF 建立可追溯的逐根台账。保留 FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API 和 openpyxl；第一阶段定位视图、绑定轴线并冻结高清图片；第二阶段按提示词将图片、规格/视图/轴线目录及当前裁切区域内的文字和矢量辅助证据交给视觉模型，程序校验台账。

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

第一阶段定位视图和第二阶段识图均需要视觉 API。在 `.env` 填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，或在网页临时填写。模型需支持图片输入以及 `response_format: json_object`。网页提供的 Key 不写入 SQLite、任务文件或导出包，仅在内存中保留至第二阶段结束，重启/第二阶段重试需重新填写或使用 `.env`。

DeepSeek 官方 API 的图纸任务使用 `deepseek-flash`，官方视觉说明：https://api-docs.deepseek.com/guides/vision/ 。首次计算默认 `reasoning_effort=high`，点击“重试/重新计算”使用 `max`，两者均显式开启 thinking；输出 token 上限分别为 65536、131072，包含思考与最终回答。实际发送参数保存在任务日志中。重算请求会留下历史记录，重启后仍使用 `max`；旧 low 响应因请求签名不同不会复用。`xhigh` 在该 API 映射为 high，更高档应使用 max，见 https://api-docs.deepseek.com/guides/thinking_mode/ 。`deepseek-v4-pro` 不在该官方视觉支持列表；旧 Pro 响应保留用于诊断，不应用作视觉验收基准。

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

手动复算可在以上命令后追加 `--reasoning-effort max`。`tools/test_image_workflow.py --reuse-prepared` 默认使用 max。只对照原文提示词与单张已保存 PNG（不修改任务）可运行 `tools/compare_member_requests.py --job-id <任务ID> --view-title "<标题>"`；最小请求不指定思考参数，遵循服务端默认，应用封装请求使用项目默认 high。每次对照固定提示词与图片哈希，原始响应保存在 `tmp/request_comparison`；不能把格式通过或单次结果差异当作准确性验收。

1. **视图、轴线或面名称和高清裁切**：原生提取后调用 AI 定位每页的独立视图，程序核对边界、排除邻图错引。这里的“轴线或面名称”包括 Alignment 立面名称与 EL 标高平面名称：图中的线表示该面在当前视图中的示意。例如同一立面可同时关联 A/B Alignment 与 EL.107.500(TOS) 等标高平面；TOS/BOB 等基准保留，不能混为一个名称。名称逐视图绑定，来自图纸，不写死项目轴号或标高。网页可编辑、确认这些名称；内部沿用 `axis_labels`/`axis_catalog` 字段兼容已有任务。目录中的面不一定有已上传的独立视图，统计分别显示名称总数与实际上传的平面/立面数量。第二阶段的 `view_catalog` 也包含这些被图纸标示、尚未上传独立视图的面，用 `uploaded:false` 区分，供 `view_tags` 引用。已有任务读取时补充未人工确认的名称显示，不改写历史结果；人工确认的名称保留。长图在本阶段生成重叠的局部放大图，供模型读清细节。
2. **逐视图构件识别**：读取第一阶段冻结的全图及同视图局部图，使用 `app/prompts/stage_two_members.md`、规格/视图/轴线目录和当前视图的文字/矢量证据，返回 spec、view_tags、member_cnt、length_mm、location、issue 六字段。`location` 用平面或立面轴线说明构件位置，不要求返回 `basis` 或计算式；斜撑由模型按提示词使用勾股定理计算。辅助证据的坐标为归一化 PDF 页坐标，仅对应裁切图片，不是工程毫米坐标；不重新定位或裁切。服务端核对目录名称、数量与长度格式，并在本地比较标签数量，不自动修正模型判断，也不能验证未返回的长度计算过程。
3. **核验与去重**：确认第二阶段后，第三阶段按规格、长度、所属面名称生成待核验组。并排查看来源图片，填写核验依据，给结果组分配各来源观察数量。保存后程序计算已确认数量；确认第三阶段后导出。旧坐标任务继续使用原匹配流程，不自动转换旧台账。

第三阶段分组规则可先离线测试，不改变网页阶段、原台账或确认记录，也不调用 API：

```powershell
.\.venv\Scripts\python.exe tools\test_group_dedup.py --job-id <任务ID>
```

工具只读 SQLite 中已完成、等待核对的第二阶段，保存输入版本快照、SHA256、运行前后一致性检查及 Markdown/JSON 报告到 `tmp/group_dedup/<任务ID>/`。按规格（保留组合身份）、面标签集合与精确长度分组；同来源数量先累计，跨来源数量一致且没有现有疑点的组只进入机械试算。数量冲突、来源覆盖差异、长度或规格分布差异、单标签/部分重叠及缺少长度均保留核验信息，未知关系不取最大值、不逐对扣减。离线试算数不是最终数量，2LF在试算中不展开。

正式第三阶段使用 `app/group_reconciliation.py`。例如来源 A 有 3 根、来源 B 有 2 根，核对发现其中 2 根重复时，建立“共同 2 根”结果组（A 分配 2、B 分配 2），再建立“A 独立 1 根”组；不能直接取最大值。每个结果组内各来源分配量必须等于组数量；所有组加排除记录不能超用任何原观察。错误观察可明确排除，必须保留原因。规格、长度或所属面修正也必须保留核验依据，原观察不被覆盖。

第三阶段网页的“AI生成分组提案（max）”会调用已有API，只发送第二阶段台账和面目录，不发送原图或验收答案。提案通过分配检查后仍需载入可编辑草稿并核对保存；已有人工修改不会被重试覆盖，新提案保留在历史中。

也可离线测试同样的提案（不修改任务）：

```powershell
.\.venv\Scripts\python.exe tools\test_group_reconciliation.py --job-id <任务ID>
```

提示词位于 `app/prompts/stage_three_groups.md`。结果保存到 `tmp/group_reconciliation/`；`validation.valid` 仅表示通过来源分配与数量守恒校验，不能代表识图准确。非法分配保留原响应和错误，不采纳为结果。提案需要核验后才能保存到正式第三阶段。

结果组保存时由服务器重新生成汇总与单根编号，2LF按组件展开一次。单根编号是组内序号，来源关联保留到组级，不代表已获得每根的精确坐标。未分配观察、未完成视图和标签覆盖差异持续列为待确认；勾选保留待确认也不会把它们计入已确认数量。上游修改会使第三阶段失效，第二阶段指纹不一致的核验草稿会被拒绝。

Excel包括规格长度汇总、单根清单、结果组来源分配、原始观察、排除原因、待确认项及版本历史；证据包保留阶段JSON、图片、模型响应和日志。程序测试通过与工程准确率分开记录；准确率必须使用独立核对的全范围参考台账评估，不能用模型自评或未去重的试算数量充当参考。

只读验收命令如下。参考JSON需包含 `members`（`spec/view_tags/length_mm/member_cnt`）和 `scope.pdf_sha256`（原文件名到SHA256的映射），全范围参考声明 `scope.complete_pdf_set: true`。先验证原PDF集合和阶段指纹，再按单根计权匹配规格、完整所属面集合和最多1mm的长度舍入差异；准确率与召回率都须达到90%，待确认长度仍计入参考分母。使用 `--proposal <离线report.json>` 可单独测试提案，不会将其标记为正式结果。

```powershell
.\.venv\Scripts\python.exe tools\score_reference.py --job-id <任务ID> --reference <独立参考.json>
```

图片保存在 `views/asset-..._300dpi.png`，局部放大图文件名含 `detail`。每次修改裁切产生新文件，历史图片保留；第二阶段直接发送已确认文件的原始 PNG 字节，记录 SHA256、尺寸、DPI 和局部图清单。第一阶段修改会使第二阶段结果失效；第二阶段不能修改已确认目录。重试不会静默覆盖人工草稿。

第二阶段标签只保留平面/立面名字：`PLAN AT EL.107.500(TOS)` 规范化为 `EL.107.500(TOS)`，`ELEVATION ON ALIGNMENT PROJECT-Q/A` 规范化为 `PROJECT-Q/A`。请求目录、响应接收、人工保存及网页标签选择均使用该形式，同义标签规范化后去重；TOS/BOB 等基准保留，不合并构件行或改变 `member_cnt`。未知面名称仍标为待确认，不能因去掉前缀就视为有效关联。原始模型响应与第一阶段图纸标题保留，日志记录响应标签规范化前后值；读取旧台账时只统一显示标签，不自动改写历史文件。

DeepSeek 官方视觉说明指出，大图推理前会按比例缩小至约 1300×1300 的像素面积：https://api-docs.deepseek.com/guides/vision/ 。所以传输 300 DPI 并不保证模型读取全部原始像素；全图加局部图仍需人工核对分段、数量与长度。

完整的真实 API 流程验证（复制源任务输入创建新任务，不改源任务，测试确认不代表工程验收）：

```powershell
.\.venv\Scripts\python.exe tools\test_image_workflow.py --source-job <源任务ID>
# 复用已完成新流程的第一阶段，单独比较图像识别效果：
.\.venv\Scripts\python.exe tools\test_image_workflow.py --source-job <新流程任务ID> --reuse-prepared
```

新任务数据包含 `workflow=image_first`；SQLite 的版本确认、人工修改历史和串行执行机制不变。识别准确性需要人工代表样本或完整基准台账验收，API 返回成功不等同于钢材识别正确。


有问题时修正或明确保留待确认项。新图片流程的第二阶段数量均为候选观察，不作为已确认工程统计。修改上游阶段会使下游失效并隐藏旧下载链接。已确认阶段也可以重新编辑，修改和确认均保留历史。

界面修改需填写修改理由。高级 JSON 编辑可调整完整数据和证据 ID；新台账允许人工修正数量、长度和构件位置，并重新执行字段与目录校验；不会用旧坐标逻辑重算这些值。历史中的人工草稿或重试提案可载入后另存为新版本。旧台账的 `basis` 在网页历史依据中保留，保存时记为 `legacy_basis`，不会猜填为 `location`；新模型响应必须使用六字段新结构，旧任务和历史文件不自动改写。

第一阶段目录由原生文字证据去重生成，独立于首尾、坐标和候选构件识别是否成功。`data.discovery_inventory` 保存规格、证据、视图目录和数量；模型提出但没有规格文字证据的项目另列为待确认，不增加已提取规格种数。已有 v2 任务读取时也会生成目录，不改变已确认版本或旧导出。当前五份验收图纸应输出 16 种规格、5 个平面、7 个立面和 1 个概览。

构件识别直接读取用户编辑的 `app/prompts/stage_two_members.md`，不附加旧的坐标/端部提示词。`stage_one.md` 只保留为视图定位及历史模块说明；实际请求提示词与契约随日志保存，变化参与缓存签名。


## 裁切检查与旧任务兼容

第一阶段自动定位采用“AI 提框＋原生证据检查”。模型同时返回结构区域、完整裁切区域、边界理由和证据 ID；程序以独立标题为锚点，将局部轴线、尺寸候选、规格及说明按到结构区域的距离分配，补入完整文字框和附近短矢量。扩边受模型区域附近的窗口限制，防止把页框轴号和右侧引用表纳入。相邻视图标题、部分图签侵入、证据歧义或无效引用转为待确认。人工已确认区域保留，不自动覆盖。

`View.localization` 和 `VIEW_REGION_CHECKED` 日志保存原始框、最终框、扩边证据及问题。新流程自动区域另存 `views/asset-..._300dpi.png`，通过视图文件接口读取，并随 ZIP 导出。AI 的裁切自评只是复核意见，不能代替原生证据覆盖检查；真实测试曾出现可见完整构件被错误报告为截断的情况。

`axis_marks`/`axis_labels` 可以包含多个 Alignment 与标高平面名称；`visible_axes` 是投影视图的两个坐标方向，两者不是同一概念。模型若同时返回 `visible_axes=[X,Y,Z]` 与明确的 `fixed_axis`，接收时仅移除重复的固定方向，记录 `VIEW_VISIBLE_AXES_NORMALIZED`，保留全部标高标记、名称、区域和原响应。未明确固定方向或其他方向冲突仍拒绝校验，不自动猜测坐标系。

以下坐标规则仅用于历史任务，不参与新的图片识别流程：

- 保存原始轴名、偏移和标高，同时求解毫米坐标。X/Y/Z 是项目全局方向，不是页面像素方向；每个平面/立面定义两个显示方向和一个固定方向，镜像/旋转不改变语义轴名。
- X/Y 轴线需要一个有证据的坐标锚点；尺寸链的 `distance_mm` 是沿全局正方向的有符号差。端点使用 `axis_label` 或 `value_mm`，两者不能同时填写；偏移可用 `towards` 指明朝向哪根轴线。
- Z 使用 EL 值和单位，相对于 `z_origin_m`。保留 TOS/BOB，只有有证据的 `offset_mm` 才能转换为其他基准。低于 TOS 100 mm 的水平斜撑必须应用偏移。缺少转换不能假设为中心线。
- 长度来自有证据的端点差，二维斜撑使用勾股关系，计算式写入审计。输出是图示定位参考线的名义长度，不是含端部切割、间隙扣减的下料长度；也不根据预拱注释推算曲线展开长度。
- 两个观察的端点匹配使用不超过 1 mm 的欧氏距离舍入容差；不会通过连续近似匹配扩大容差，不按规格/长度分组去重。
- 相交、同轴和共线本身不能证明首尾边界。明确端部、拼接或规格变化才形成可确认分段；连续穿过一个轴线的柱不自动按楼层拆分。
- `2LF`/`2L` 先作为组合实体去重，再展开带不同组件编号的单根角钢。最终汇总仅显示单根数量，组合关系保留在 Member Register。

首版只支持有文字和矢量的 PDF、横平竖直构件和二维斜撑。扫描件及三维斜撑保留为不支持/待确认。当前视图辅助证据以紧凑格式最多提供 1600 条较长矢量原语，同时提供该区域文字及位置；完整精度和原语仍写入 native，截取会产生覆盖警告。请求日志记录实际发送的文字条数、矢量条数、原矢量总数及截断状态；完整输入包另存于 responses。模型识别是否完整需要逐阶段核对，不能以原语数或模型返回数量作为人工基准。

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
| `GET /api/jobs/{id}/views/{filename}` | 页面预览 JPEG、冻结的全图及局部 PNG |
| `GET /api/jobs/{id}/download` | 已确认的 Excel |
| `GET /api/jobs/{id}/views-download` | 已确认的截图/坐标/证据 ZIP |
| `GET /api/jobs/{id}/logs?after=0&limit=200` | 分页读取可解释审计事件（JSONL 游标） |
| `GET /api/jobs/{id}/logs-download` | 下载完整脱敏任务日志，执行中也可使用 |

旧 revision 返回 409；无效坐标/证据或未明确允许的待确认项返回 422。重复确认不会重复创建阶段或导出。任务执行中不能修改草稿。模型给出的描述性视图/观察 ID 在校验前被替换为服务端 ID，不能用作文件名。

SQLite 位于 `data/jobs_v2.sqlite3`，存储任务、阶段、版本、历史和事件；重启后将排队/执行中的工作标记为 interrupted，需手动重试。第一阶段重试会重新提取并定位视图，可能调用 API；新图片采用独立文件保留人工版本。失败/中断的第二阶段重试会重新校验并复用已保存的匹配响应，只请求尚未完成的识别；新请求以模型、地址、提示词、契约、PDF 内容和裁切区域的哈希签名匹配。旧响应无签名时仅在上传文件未修改时恢复。API 重试可传 `reuse_saved:false` 强制重新请求；等待人工确认时的普通重试仍重新识别。重试人工编辑过的草稿时，新模型结果保存为历史提案，人工草稿不被覆盖。旧版第一阶段已有观察的任务保留原数据，第二阶段可继续程序计算，不自动重跑 AI。

`data/jobs/<id>/` 保存 uploads、native、views、responses 和 output。ZIP 包含三个阶段的 JSON、原始文字/矢量、图像、原始响应和人工修改历史。Excel 包含数量长度汇总、逐根台账、观察台账、长度计算、匹配、异常、历史和覆盖说明。

## 可解释日志与版本管理

网页的“处理依据与 API 日志”提供请求耗时、响应校验、ID 规范化、证据修复、长度计算式、坐标基准、匹配理由和排除原因。完整事件写入任务目录的 `logs/events.jsonl`；实际请求的提示词与契约写入对应 `.prompt.txt`，原始响应与请求哈希分别写入 `.json` 和 `.request.json`。这些文件随最终 ZIP 导出。

模型的 ID 在领域校验前由服务端替换。模型缩短的矢量 ID 只有在唯一对应一个原始线段时才补全；无法唯一对应的证据和越界首尾转为待确认，并保留原始响应。`discovery_checkpoint.json` 记录已识别视图的进行中检查点，不代表确认数量。

历史坐标模块中，缺失端部证据的观察会在第二阶段额外复核，要求逐观察返回首尾类型、原始证据 ID 与具体理由；原识别和复核响应分别保存，日志记录接受或拒绝的原因。平面中的柱截面不能证明柱的纵向首尾。复核不会修改数量、规格或坐标，也不以规格文字单独证明端部。

轴线全名与短名保留对应关系。模型同时返回轴线与等值毫米坐标时，仅消除等值冗余；不一致值仍阻止确认。直接坐标只有在匹配已标定轴线，或关联的尺寸证据支持偏移时才能改写为语义坐标。过程保留修改前后值和证据，写入日志、JSON 和 Length Audit，不能拿视图其他位置碰巧相同的数字补充偏移依据。

旧坐标流程的第二阶段暂停任务可单独补做端部复核（调用 `.env` 中的 API，保存提案后以阶段 revision 校验更新草稿；旧版第一阶段已有观察的任务需加 `--stage 1`，新目录阶段禁止端部复核）：

```powershell
.\.venv\Scripts\python.exe tools\review_task_boundaries.py --job-id <任务ID>
```

加 `--repair-empty-views` 时只恢复没有观察的平面/立面，再复核其端部；可加 `--view-id` 限定一个视图。原始提示词、截断响应和恢复响应分别保留。DeepSeek Flash 输出出现 `finish_reason=length` 时，程序保留 `.truncated.json`，由 high 升为 max 并提高输出额度后仅重试一次，始终保持思考开启；max 仍截断则失败，不采纳部分结果。构件 JSON 格式重试同样使用 max，不静默降级。

API 地址、Key、认证头不作为日志字段保存；已知密钥、Bearer 值和 URL 中的认证参数在日志及错误堆栈中脱敏。长请求每 30 秒记录等待事件，结束后记录耗时、token usage、finish_reason 和响应文件，以区分 API 等待、返回空内容和结构校验失败。

项目已建立本地 Git 仓库。`.gitignore` 排除 `.env` 与其变体、虚拟环境、缓存、任务目录、SQLite、日志、PDF、Excel 和 ZIP；仅空白 `.env.example` 跟踪入库。没有向远端上传代码或任务数据。

使用真实图纸做程序验收时，阶段确认可携带 `reason`，例如“流程验证，允许待确认项继续；不是工程确认”。完成状态只表示程序完成三阶段及导出，不代表待确认构件已得到工程认可。

仅对旧坐标流程中已经执行的任务进行三阶段程序验收（会自动确认阶段，明确保留疑点）：

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

第二个命令将两个高清 PNG、2600 像素预览及报告写入 `tmp/pdf_render_test/`（Git 忽略）。`render_png(entry, dpi=300)` 直接从 PDF 矢量渲染，无损输出且不受预览尺寸限制；新图片流程直接发送第一阶段确认的 PNG，网页旧任务预览仍可使用 JPEG。

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
