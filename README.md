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

在 `.env` 填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，或在网页临时填写。模型需支持图片输入以及 `response_format: json_object`。网页提供的 Key 不写入 SQLite、任务文件或导出包；第一阶段执行结束后释放，重启/重试需重新填写或使用 `.env`。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8120
```

打开 http://127.0.0.1:8120 。使用单个 Uvicorn 进程，不加 `--workers`；模型任务按锁串行运行。整个应用的实际根目录是本 README 所在目录，不是外层同名目录。

## 三阶段确认

1. **规格和候选构件**：上传 PDF，仅运行第一阶段。核对每个独立视图、图纸轴名和规格；原图覆盖显示候选构件，可指定图上首尾、修改视图区域、添加/删除构件。概览只用于覆盖检查，底板和连接详图不计数。
2. **首尾和名义长度**：确认第一阶段后，程序用尺寸链、标高和显式偏移求解坐标，生成每个观察的长度及规格/长度目录。核对端部、拼接和基准；图上的点不能代替尺寸证据。可编辑轴线坐标、语义端点、偏移方向和分段，保存时重算所有长度。
3. **实体匹配和单根数量**：确认第二阶段后，比较跨视图观察。相同无方向端点、规格、参考基准及组合/分段身份匹配为同一实体。相邻、部分重叠、同视图重叠或规格冲突显示对照图，可确认同一根、保留独立构件或留待确认。最后确认才导出 Excel 和 ZIP。

有问题时修正或勾选“保留待确认项继续”。无法确认坐标、长度或匹配的观察不纳入已确认汇总；覆盖警告保留在导出的 Method Notes。修改上游阶段会使下游失效并隐藏旧下载链接。已确认阶段也可以重新编辑，修改和确认均保留历史。

界面修改需填写依据，保存为带页面位置的人工证据。高级 JSON 编辑可调整完整数据和证据 ID；填写的候选长度、confirmed 标志和最终数量不会被直接采信，而会重新计算。历史中的人工草稿或重试提案可载入后另存为新版本。

## 坐标与长度口径

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
| `POST /api/jobs/{id}/stages/{stage}/approve` | `{revision, acknowledge_pending}`，确认并启动下阶段；第三阶段启动导出 |
| `POST /api/jobs/{id}/stages/{stage}/retry` | `{revision, settings?: {base_url,api_key,model}}`，显式重试当前阶段或中断的导出 |
| `GET /api/jobs/{id}/pages` | 原始页面预览与标题候选 |
| `GET /api/jobs/{id}/evidence?page_id=...&view_id=...&q=...` | 按文字/ID 查找定位证据，默认最多 200 条 |
| `GET /api/jobs/{id}/views/{filename}` | 页面或视图 JPEG |
| `GET /api/jobs/{id}/download` | 已确认的 Excel |
| `GET /api/jobs/{id}/views-download` | 已确认的截图/坐标/证据 ZIP |
| `GET /api/jobs/{id}/logs?after=0&limit=200` | 分页读取可解释审计事件（JSONL 游标） |
| `GET /api/jobs/{id}/logs-download` | 下载完整脱敏任务日志，执行中也可使用 |

旧 revision 返回 409；无效坐标/证据或未明确允许的待确认项返回 422。重复确认不会重复创建阶段或导出。任务执行中不能修改草稿。模型给出的描述性视图/观察 ID 在校验前被替换为服务端 ID，不能用作文件名。

SQLite 位于 `data/jobs_v2.sqlite3`，存储任务、阶段、版本、历史和事件；重启后将排队/执行中的工作标记为 interrupted，需手动重试。失败/中断的第一阶段重试会重新校验并复用已保存的匹配响应，只请求尚未完成的识别；新请求以模型、地址、提示词、契约、PDF 内容和裁切区域的哈希签名匹配。旧响应无签名时仅在上传文件未修改时恢复。API 重试可传 `reuse_saved:false` 强制重新请求；等待人工确认时的普通重试仍重新识别。重试人工编辑过的草稿时，新模型结果保存为历史提案，人工草稿不被覆盖。

`data/jobs/<id>/` 保存 uploads、native、views、responses 和 output。ZIP 包含三个阶段的 JSON、原始文字/矢量、图像、原始响应和人工修改历史。Excel 包含数量长度汇总、逐根台账、观察台账、长度计算、匹配、异常、历史和覆盖说明。

## 可解释日志与版本管理

网页的“处理依据与 API 日志”提供请求耗时、响应校验、ID 规范化、证据修复、长度计算式、坐标基准、匹配理由和排除原因。完整事件写入任务目录的 `logs/events.jsonl`；实际请求的提示词与契约写入对应 `.prompt.txt`，原始响应与请求哈希分别写入 `.json` 和 `.request.json`。这些文件随最终 ZIP 导出。

模型的 ID 在领域校验前由服务端替换。模型缩短的矢量 ID 只有在唯一对应一个原始线段时才补全；无法唯一对应的证据和越界首尾转为待确认，并保留原始响应。`discovery_checkpoint.json` 记录已识别视图的进行中检查点，不代表确认数量。

API 地址、Key、认证头不作为日志字段保存；已知密钥、Bearer 值和 URL 中的认证参数在日志及错误堆栈中脱敏。长请求每 30 秒记录等待事件，结束后记录耗时、token usage、finish_reason 和响应文件，以区分 API 等待、返回空内容和结构校验失败。

项目已建立本地 Git 仓库。`.gitignore` 排除 `.env` 与其变体、虚拟环境、缓存、任务目录、SQLite、日志、PDF、Excel 和 ZIP；仅空白 `.env.example` 跟踪入库。没有向远端上传代码或任务数据。

使用真实图纸做程序验收时，阶段确认可携带 `reason`，例如“流程验证，允许待确认项继续；不是工程确认”。完成状态只表示程序完成三阶段及导出，不代表待确认构件已得到工程认可。

旧任务文件保留，不转换成 v2；旧 Excel 不能自动成为新坐标台账。`llm_runner.py` 和旧规则测试仅保留历史校验能力，Web 任务入口使用新的 pipeline。

## 验证

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
