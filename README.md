# LLM Count

从钢结构 PDF 建造布置图自动生成规格、名义长度与数量台账。使用 FastAPI、原生 JavaScript、PyMuPDF、OpenAI 兼容 API、SQLite 和 openpyxl。

## 工作流程

1. **视图与标签**：AI 定位平面、立面区域；程序提取原生钢材标签、轴线或面名称，生成 300 DPI 高清图，每个标签保留独立身份。
2. **尺寸识别**：网格子代理与总代理结合第一阶段事实识别尺寸，按需调用专家和仲裁。斜撑由模型提供直角边依据，本地计算长度；程序补入图纸标题所属面。
3. **匹配与去重**：跨视图匹配、消歧，仅选择来源记录已有的长度。同一视图不同标签保留独立数量，组合角钢最后展开一次，导出 Excel 和证据包。

网页默认自动完成三阶段，也可逐阶段暂停或只运行前两阶段。可查看视图目录、高清图、数量长度汇总、逐标签台账与日志；当前固定标签台账只读。完成后可从第二阶段重新执行，复用第一阶段，旧结果保留为历史。

适用于可提取文字的矢量 PDF；输出为图示名义长度。扫描件及加工下料长度不在当前范围。

## 启动（Windows PowerShell）

在本 README 所在目录执行：

```powershell
# 已安装 uv 可跳过
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# 仅首次配置，已有文件不要覆盖
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8120
```

在 `.env` 填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，模型需支持图片输入和 JSON 输出。打开 [任务页面](http://127.0.0.1:8120/)，上传 PDF 即可运行。旧 `/workflow` 地址自动返回原阶段页面。

- 思考强度默认 `high`，重试可选 `low / high / max`。
- `LLM_TASK_CONCURRENCY=10`：第一阶段并发文件数、第二阶段同时执行的视图 task 数。
- `LLM_MAX_CONCURRENCY=5`：每个视图内部的代理请求上限；不是全任务总上限。第二阶段理论上最多 10×5 个请求，任务之间串行。
- `LABEL_LENGTH_REVIEW_BATCH_SIZE=6`：第二阶段专家/仲裁单次目标数，范围 1–12。
- `ENTITY_MAX_CALLS=24`：第三阶段调用预算，包含失败重试；预算耗尽保留回退结果与原因。

同一数据目录只启动一个服务进程。更新代码应等待任务结束再重启；中断后显式重试，可复用校验通过的已有结果。任务、原图、模型回复和日志保存在 `data/`，不提交到 Git；网页覆盖的 API Key 仅保存在服务内存。

## 最新实测指标（2026-10-24）

02、03、04、05 四份 PDF **从上传到导出全新运行**，共 11 个视图。以用户最新提供的型钢清单离线评分，基准未传入模型，也未用于修改结果。本轮指已完成的任务 `a3d9c185…`；后续新测试已按用户要求中断，尚无新全量成绩。

| 指标 | 结果 |
| --- | ---: |
| 参考单根 / 输出单根 / 匹配 | 310 / 339 / 260 |
| 精确率 Precision | **76.70%** |
| 召回率 Recall | **83.87%** |
| F1 | **80.12%** |
| 钢材种类覆盖；种类精确率 / 召回率 | 16/16；100% / 100% |
| UNKNOWN 单根 | 0 |
| 未匹配输出 / 未匹配基准（含错长） | 79 / 50 |
| 全流程耗时 | 2884.19 秒（约 48 分钟） |
| API 请求 / 并发峰值 | 135 / 5 |

评分按“规格＋长度数值”多重集合完全匹配，零容差、不额外舍入，组合角钢展开一次；不计4条PL记录，材质未输出不评价，也不代表逐位置身份准确率。第三阶段有 56 根/组合采用失败或预算回退，重复仍可能保留。主要差异集中在斜撑长度与重复保留。UNKNOWN 为零不代表没有错误；原自建名义长度参考表的94.92% F1属于旧评分口径。

另一次 02 单文件历史运行使用当时原图名义长度参考表，90/90根匹配；不能外推到所有图纸或当作本轮新基准全量成绩。任务编号、评分依据及详细用量见 [网页验收与指标说明](docs/native-workflow-web.md)。

## 测试与文档

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/*.cjs
```

本次回归：206 项 Python 测试、5 项 Node 测试通过。已有导出可独立离线评分，不调用 API：

```powershell
.\.venv\Scripts\python.exe tools/evaluate_entity_run.py --result <第三阶段目录> --baseline <参考.xlsx> --logs-root <任务目录>
```

- [当前执行链与每阶段交接说明](docs/current-workflow.md)
- [更新日志](CHANGELOG.md)
- [网页操作、接口与真实验收](docs/native-workflow-web.md)
- [原生标签与轴面提取](docs/pdf-label-tags-experiment.md)
- [固定标签第二阶段与提示词](docs/label-constrained-lengths.md)
- [跨视图匹配与去重](docs/cross-view-entities.md)
