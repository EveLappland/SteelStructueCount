# LLM Count

将钢结构 PDF 平面、立面布置图转换为“规格、名义长度、单根数量”台账。技术栈为 FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API、SQLite 和 openpyxl。

## 当前功能

1. **视图与标签**：AI 定位建造视图；程序提取原生钢材标签、完整面名、轴面关系和尺寸文字，生成 300 DPI PNG。每个标签保留独立 ID。
2. **长度识别**：重叠网格子代理 → 总代理 → 按需长度专家 → 冲突仲裁。模型引用原生尺寸文字，程序计算直段、尺寸差和二维斜撑长度，保留两位小数。
3. **跨视图匹配**：只匹配、去重和选择已有长度来源，不自由重算。同一视图中的不同标签保留独立数量；组合件去重后只展开一次。

网页默认自动完成三阶段并导出 Excel、图片与证据包，也支持逐阶段暂停、查看历史、失败重试，以及完成后从第二阶段重新执行。当前固定标签台账**只读**；UNKNOWN 和匹配失败的回退记录保留数量与原因，流程可以自动导出。

最终 Excel 的“单根材料清单”仅含 `PROFILE / MATERIAL / LENGTH(mm)`，每根展开单件一行，角钢使用单件规格。当前没有逐根材质数据，MATERIAL 留空。

输入须能提取原生文字与矢量，并满足“每根构件/组合都有规格标签”的前提。输出为图示名义长度；扫描件、加工下料长度及三维斜撑不在当前范围。标签邻近归属与模型尺寸解释仍可能出错。

## 安装与启动

在本 README 所在目录，用 Windows PowerShell 执行：

```powershell
# 尚未安装 uv 时执行，安装后重新打开终端
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`；所选模型和服务需接受图片输入及结构化 JSON 请求。然后启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8120
```

打开 [任务页面](http://127.0.0.1:8120/)。同一数据目录使用一个服务进程；更新代码后应等待运行结束再重启，避免后端旧进程与新静态文件混用。

## 并发与配置

| 设置 | 默认值 | 当前作用 |
| --- | ---: | --- |
| `LLM_TASK_CONCURRENCY` | 10 | 第一阶段文件 task、第二阶段视图 task、第三阶段匹配组的并发上限 |
| `LLM_MAX_CONCURRENCY` | 5 | **每个第二阶段视图内部**的代理请求上限 |
| `LABEL_LENGTH_REVIEW_BATCH_SIZE` | 6 | 专家/仲裁每批目标标签数，范围 1–12 |
| `ENTITY_MAX_CALLS` | 24 | 第三阶段每次执行的请求预算，包含失败重试 |

任务之间排队；一个任务内，全部第一阶段交付后才进入第二阶段，全部第二阶段交付后才进入第三阶段。第二阶段默认理论上限为 **10 × 5 个同时请求**，实际受代理依赖与工作量限制。思考强度默认 `high`，可选 `low / high / max`，具体支持取决于代码中的模型适配。详见 [配置说明](docs/configuration.md)。

## 最近一次运行

任务 `0094b0c826e447de953f34d8672503d1` 于香港时间 2026-10-24 20:09:17–20:32:14 从第二阶段重跑，耗时 **22 分 56.8 秒**：第二阶段约 20 分 40 秒，第三阶段及导出约 2 分 16.7 秒。第一阶段复用；354 条标签全部得到长度，第三阶段 UNKNOWN 为 0、回退保留 10 根/组合。该次运行的评分尚未与下方新报告绑定，不能把 UNKNOWN 为 0 当作准确率 100%。[耗时口径与首轮请求失败原因](docs/validation.md)。

## 最新测试指标

2026-10-27 收录用户提供的最新测试结果，使用 **DeepSeek API**，报告耗时 **20 分 39.8 秒**、花费 **9.48（币种未注明）**。耗时的阶段范围与 job ID 尚未提供，和上面的历史重跑墙钟统计分别记录。

| 0 mm 容差指标 | 结果 |
| --- | ---: |
| 标准 / 预测 / 匹配单根 | 310 / 316 / 310 |
| TP / FP / FN | 310 / 6 / 0 |
| Precision / Recall / F1 | 98.10% / 100.00% / 99.04% |
| 钢材总数量准确率 / 报告 Accuracy | 98.10% / 98.10% |
| 规格覆盖率 / 规格精确率 | 100.00% / 100.00%（16 / 16 种） |

长度错误 0、缺失 0、增加 6：HEA260 多 2 根、HEA340 多 1 根、HEA500 多 2 根、HEB550 多 1 根。5% 相对长度差仅用于未匹配项的错误分类，不改变 0 mm 匹配容差。

局限：本轮未利用 API Cache 机制，自动命中情况未核验；评判需多次迭代增强准确性，其他 API 与本地部署模型尚未测试。代码已有本地已保存响应复用，区别于服务商 Cache。本次仅更新文档，未独立重跑评分；[逐规格指标、计算口径与历史成绩](docs/validation.md) 已分别保留。

## 开发与文档

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/*.cjs
```

最近一次代码回归为 203 项 Python、5 项 Node 测试通过；程序一致性测试不代表模型识别准确率。当前任务、原图、响应和日志保存在被 Git 忽略的 `data/`，不要提交 `.env` 或验收清单。

- [文档目录](docs/README.md) · [网页操作](docs/native-workflow-web.md)
- [架构与兼容边界](docs/architecture.md) · [接口](docs/api.md)
- [第一阶段](docs/native-label-catalog.md) · [第二阶段及 Prompt](docs/label-constrained-lengths.md) · [第三阶段](docs/cross-view-entities.md)
- [更新日志](CHANGELOG.md) · [开发交接](docs/current-workflow.md)
