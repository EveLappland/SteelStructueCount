# 固定标签流程网页

## 启动与操作

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8120
```

访问 http://127.0.0.1:8120/workflow 。旧入口 `/` 的任务、数据库和文件保持可读。

网页和 CLI 共用 `experiments/pdf_label_tags/run.py:run`、`app/label_length_workflow.py:run`、`app/entity_workflow.py:run`，没有网页专用识别答案或独立的去重算法。

上传 PDF 自动完成一至三阶段；只想检查新版第三阶段，可选择已有第二阶段包。来源目录从 `tmp/label_lengths/` 和 `data/native_runs/` 发现，不接受浏览器任意文件路径；目录名、轴面名称与数量不参与识别规则。重复 PDF 视图、篡改标签、长度或图片在请求前拒绝。

每条输入标签保留独立身份。第三阶段仅匹配与选择已有长度来源，不新增尺寸表达式；同一视图的不同标签不能合并为一根。单面标签原样保留。UNKNOWN 和失败回退保留数量与原因，自动导出，无需人工审批。

思考强度默认 high；并发未填写时使用 `LLM_MAX_CONCURRENCY`，默认 5；第三阶段预算使用 `ENTITY_MAX_CALLS`，默认 24。预算耗尽时任务仍导出，页面明确显示回退数量。第三阶段不同于第二阶段的专家/仲裁，不进行自由重算。

任务之间串行，任务内请求并发。SQLite 状态及模型原始响应均落盘，浏览器刷新后可恢复显示。服务启动才执行中断恢复，普通模块导入不修改运行状态。任务完成、失败或中断后可重新执行，新建任务保留旧结果。相同请求编号重复提交不重复启动。

## 接口

- `GET /workflow`：新版网页。
- `GET /api/native/sources`：可选第二阶段包及其视图、数量。
- `POST /api/native/runs/pdf`：上传 PDF，Form 参数 `reasoning_effort`、`max_concurrency`、`max_calls`。
- `POST /api/native/runs/match`：JSON 包含 `source_ids`、`request_id` 及上述参数。
- `GET /api/native/runs`、`GET /api/native/runs/{id}`：历史与状态。
- `POST /api/native/runs/{id}/rerun`：终态任务重新执行，JSON 包含新的 `request_id` 及设置。
- `GET /api/native/runs/{id}/logs`：运行日志。
- `GET /api/native/runs/{id}/files/{filename}`：限定在本任务目录的报告、高清图和导出文件。

工作文件存于 `data/native_runs/{id}`，状态库 `data/native_runs.sqlite3`。这些文件和 `.env` 均被 Git 忽略，API Key 不持久化到状态或日志。一个数据目录只启动一个 Uvicorn 进程。

## 离线评分

评分工具不会被模型工作流导入，参考表在识别结束后离线使用。它复用用户提供的 `tools/judge.py` 规格规范化和多重集匹配算法，UNKNOWN 保留在分母中，长度容差 ±1mm。不会按参考总数增删记录。

```powershell
.\.venv\Scripts\python.exe tools/evaluate_entity_run.py --result data/native_runs/<id>/stage_three --baseline <reference.xlsx> --logs-root data/native_runs/<id>
```

输出 `stage_three/evaluation/index.html` 与 `metrics.json`；刷新任务网页可打开该轮评分。规格+长度数量评分不能证明逐位置对应正确。既有原图核对表尚非用户最终确认的工程基准。

## 2026-10-24 网页真实验收

通过网页勾选 02、03、04、05 的既有第二阶段包，发起任务 `bfcb65ef3fd341a8972cd03776af0e48`。第一、二阶段复用并严格校验，新版第三阶段全部真实调用；无旧响应缓存、无人工改台账。

| 指标 | 结果 |
| --- | ---: |
| 视图 / 输入观察 | 11 / 354 |
| 实体/组合 / 展开单根 | 271 / 311 |
| 去掉重复观察 | 83 |
| UNKNOWN 输入 → 输出 | 4 → 0（仅继承匹配来源长度） |
| 失败回退 / 格式重试 | 0 / 0 |
| API 请求 / 实际并发峰值 | 23 / 5 |
| 第三阶段耗时 | 153.360 秒 |
| Prompt / Completion / Total tokens | 342905 / 101596 / 444501 |
| 参考单根 / 匹配 | 310 / 294 |
| Precision / Recall / F1 | 94.53% / 94.84% / 94.69% |
| 钢材种类覆盖 / 种类 P、R | 16/16 / 100%、100% |

参考文件 `tmp/full_economy_02_05_repaired/evaluation/nominal_reference.xlsx`，SHA256 `b4f7ce02006879a896119baa0cb923a8ac8d57b0df6bb5559a3c241e847ad1d6`。

与旧版同输入全量 F1 94.36% 比较，提升约 0.32 个百分点，不能据此宣称普遍提升。12 种规格完全匹配；剩余为 HEA300 两根错长、IPET270 六根错长、L100*10 与 L80*8 各四根错长，以及 HEB550 多计一根。UNKNOWN 清零不表示工程错误清零。

354 条观察恰好分配一次、逐视图数量保留、第二阶段和图像指纹不变、最终测量均取自实体来源观察、组合仅展开一次，全部通过本地核验。

另通过网页上传 02 PDF，全新执行一至三阶段，任务 `75944672166c484ebf6430f68bd64851`：2 个视图、90 个标签、90 个已知长度实体，无重复删除、UNKNOWN 或回退。以同一独立原图核对表中对应面筛出的 90 根作离线参考，规格+名义长度全部匹配，P/R/F1 均 100%，10 种规格全部覆盖。

02 全流程共 18 次真实 API 调用（第一阶段 1、第二阶段 17、第三阶段 0），并发峰值 5，无格式重试，墙钟 563.31 秒，609849 tokens。第二阶段 558.594 秒；第一阶段与最终核验、导出计入全流程时间。与上述四 PDF 重跑第三阶段为两个独立验收范围；不能说四份 PDF 本轮都从头重跑。

浏览器实际验证上传、选择来源、阶段高亮、第一/二/三阶段报告、任务历史、Excel 下载及离线评分链接。下载文件与服务端 Excel SHA256 一致。任务完成后重启服务，两个任务与结果均恢复可读，未产生额外付费请求。新增同 PDF 多版本时，快捷选择只取不重叠的最新结果，服务端仍拒绝重复 PDF 视图。

200 项 Python 测试和 5 项 Node 测试通过；没有使用模拟 API 回答证明识别效果。单元测试中的虚构局部数据只验证契约、守恒、文件边界、UNKNOWN 分母与组合展开，不计入上述准确率。
