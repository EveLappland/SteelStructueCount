# LLM Count

从钢结构 PDF 建造布置图建立规格、名义长度和数量台账。FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API、SQLite 和 openpyxl。默认自动执行三个阶段并导出，也可逐阶段暂停。

## 当前流程

1. `app/stage_one.py`：Agent 按页判断用途，只对包含平面/立面建造布置的页面定位视图、枚举面名称和钢材种类。概览、节点、底板和说明等保留跳过理由。有效视图生成 300 DPI PNG，长图另生成重叠局部图。
2. `app/image_workflow.py`：按视图并发，每个视图只做一次识图调用，发送高清图、局部图和第一阶段目录。原生文字/矢量留在本地。程序补入来源视图标签，按直角边计算斜撑长度、保留两位小数。标签数与 `member_cnt` 合计的差异只记录，不触发再次识图或自动增删构件。
3. `app/group_proposal.py` / `app/group_reconciliation.py`：后端 AI 解释跨视图重复关系，程序验证来源分配及数量守恒，分配错误最多请求两次修正。目录外规格、缺失长度和未完成来源保留待确认；通过检查后展开组合角钢并导出。

各视图地位相同。重复关系不确定时模型按独立观察保留风险，不由程序凑数。程序检查通过不等于工程准确性验收。

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

`.env` 配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，可设置 `LLM_MAX_CONCURRENCY=3`。模型须支持图片和 JSON。网页可覆盖配置，API Key 只放在内存。默认思考强度 high，手动重试可选 low / high / max。

打开 http://127.0.0.1:8120 。只运行一个 Uvicorn 进程。阶段之间串行，第一阶段按页、第二阶段按视图并发，最终 PDF 渲染串行。更新代码后等待任务结束再重启服务，已有结果不会自动重算。

## 接口、数据和追溯

- `POST /api/jobs` 上传 PDF，默认 `auto_run=true`；false 则逐阶段暂停。
- `GET /api/jobs/{id}` 查询状态、版本和下载链接。
- `GET/PATCH /api/jobs/{id}/stages/{stage}` 读取/修改草稿。
- `POST /api/jobs/{id}/stages/{stage}/approve`、`retry` 确认/重试，携带 `revision` 防旧版本覆盖。`reuse_saved=true` 仅复用请求指纹一致、重新校验通过的响应。
- `/pages`、`/views/{filename}` 页面目录、按需预览和冻结高清图。
- `/logs`、`/logs-download`、`/download`、`/views-download` 审计、Excel 和证据包。

SQLite 保存版本、草稿、修改理由和状态；`data/jobs/{id}` 保存 PDF、图片、模型响应和日志。上游修改使下游失效；重试不覆盖人工草稿。结果组 ID 格式修复保留映射，原响应及观察引用不改写。

任务全部完成后，可切换到第二阶段，点击“从第二阶段重新执行”。保留第一阶段视图、目录和高清裁图，重新调用 AI 识别；旧第二阶段台账（含人工修改）存入历史，本次采用新结果，第三阶段和下载链接失效后重新生成。自动任务继续执行到导出，逐阶段暂停任务在第二阶段完成后等待确认。此操作不复用旧识图响应，可在按钮旁选择思考强度。

Excel 包含汇总、单根台账、组分配、原始观察、待确认项、用途说明和历史。ZIP 包含截图、原生证据、响应和审计 JSON。旧坐标和旧定位流程已停用，历史文件和记录保留，可查看完整 JSON、下载已有结果。清理依据见 [docs/refactor-audit.md](docs/refactor-audit.md)。

## 提示词

当前仅有四份：`page_purpose.md`（页面用途）、`construction_catalog.md`（定位与目录）、`stage_two_members.md`（识图）、`stage_three_groups.md`（分配核验），均位于 `app/prompts/`。

第二阶段 `view_tags` 只返回所在轴线/标高面，允许空数组，程序补入图纸标题所在面。去除 PLAN AT 等展示前缀，保留 TOS/BOB 基准。`member_cnt` 是同规格、长度、标签的构件段数量；2LF / 2F / 2L 先计组合，最后展开一次。

## 测试和真实验收

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
$env:PYTHONPATH = "$PWD;$PWD\tests"
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/*.cjs
```

单元测试不调用付费 API，覆盖渲染、长度、并发、来源标签、版本控制、恢复、失败重试和数量守恒。

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

