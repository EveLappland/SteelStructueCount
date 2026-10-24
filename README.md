# LLM Count

将钢结构 PDF 平面、立面布置图转换为“规格、名义长度、单根数量”台账。技术栈为 FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API、SQLite 和 openpyxl。

## 当前功能

1. **视图与标签**：AI 定位建造视图；程序提取原生钢材标签、完整面名、轴面关系和尺寸文字，生成 300 DPI PNG。每个标签保留独立 ID。
2. **长度识别**：重叠网格子代理 → 总代理 → 按需长度专家 → 冲突仲裁。模型引用原生尺寸文字，程序计算直段、尺寸差和二维斜撑长度，保留两位小数。
3. **跨视图匹配**：只匹配、去重和选择已有长度来源，不自由重算。同一视图中的不同标签保留独立数量；组合件去重后只展开一次。

网页默认自动完成三阶段并导出 Excel、图片与证据包，也支持逐阶段暂停、查看历史、失败重试，以及完成后从第二阶段重新执行。当前固定标签台账**只读**；UNKNOWN 和匹配失败的回退记录保留数量与原因，流程可以自动导出。

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

## 最近已完成的严格评分

02–05 四份 PDF、11 个视图，任务 `a3d9c18554eb4b90bf63277022de7555`。用户清单仅在导出后离线使用，按规格文本及长度数值**完全匹配**计分，组合展开一次，不含 4 条 PL 记录，不评价材质或逐位置身份。

| 参考 / 输出 / 匹配单根 | Precision | Recall | F1 | 种类覆盖 |
| --- | ---: | ---: | ---: | ---: |
| 310 / 339 / 260 | 76.70% | 83.87% | 80.12% | 16/16 |

该轮耗时 2884.19 秒，135 次 API 请求，实际峰值 5；它发生在两层并发调整之前，**不能作为当前 10×5 调度的性能成绩**。UNKNOWN 为 0，但第三阶段有 56 根/组合回退保留；仍存在错长和重复。旧名义长度参考表的 94.92% F1 属于不同评分口径。基准指纹、错漏及评分工具限制见 [测试与指标](docs/validation.md)。

## 开发与文档

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/*.cjs
```

最近一次代码回归为 206 项 Python、5 项 Node 测试通过；程序一致性测试不代表模型识别准确率。当前任务、原图、响应和日志保存在被 Git 忽略的 `data/`，不要提交 `.env` 或验收清单。

- [文档目录](docs/README.md) · [网页操作](docs/native-workflow-web.md)
- [架构与兼容边界](docs/architecture.md) · [接口](docs/api.md)
- [第一阶段](docs/pdf-label-tags-experiment.md) · [第二阶段及 Prompt](docs/label-constrained-lengths.md) · [第三阶段](docs/cross-view-entities.md)
- [更新日志](CHANGELOG.md) · [开发交接](docs/current-workflow.md)
