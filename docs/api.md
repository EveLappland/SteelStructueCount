# 当前网页接口

实现见 [native_web.py](../app/native_web.py)。新上传任务使用 `/api/native`，不能用旧 `/api/jobs` 的返回结构、PATCH 或阶段版本字段替代。

服务入口：[http://127.0.0.1:8120/](http://127.0.0.1:8120/)；FastAPI 在线接口文档为 [http://127.0.0.1:8120/docs](http://127.0.0.1:8120/docs)。下面仅描述当前固定标签流程。

## 上传与启动

`POST /api/native/runs/pdf`，类型 `multipart/form-data`：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `files` | 必填、可重复 | 上传 PDF 文件内容，不接收客户端本地路径 |
| `reasoning_effort` | `high` | `low / high / max` |
| `max_task_concurrency` | 环境配置 | 主 task 并发正整数 |
| `max_concurrency` | 环境配置 | 每个第二阶段视图请求并发正整数 |
| `max_calls` | 环境配置 | 第三阶段本次执行预算，包含重试 |
| `auto_run` | `true` | 自动进入后续阶段；false 则逐阶段等待确认 |
| `stop_after_stage` | `3` | 推荐 1、2、3；兼容页面旧值 4，内部按 3 处理 |
| `base_url, api_key, model` | 环境配置 | 可选服务覆盖，保存在服务内存 |

成功创建后返回包含 `id` 的任务记录，并进入执行队列；后续轮询状态接口。空/加密/无效 PDF、非 PDF 文件或本次重复上传相同 PDF 会被拒绝。该接口收到并接受文件后会执行付费工作流。

当前处理器不读取旧页面的 `label_color_mode` 字段，不应通过它判断模型收到的是着色图。

## 查询与文件

| 方法与路径 | 返回/用途 |
| --- | --- |
| `GET /api/native/capabilities` | 阶段操作、只读台账、UI、并发模型及环境默认上限 |
| `GET /api/native/runs` | 新流程任务历史元数据 |
| `GET /api/native/runs/{id}` | 页面适配后的状态、事件、阶段元数据、汇总与下载入口 |
| `GET /api/native/runs/{id}/stages/{stage}` | 指定阶段数据，stage 为 1–3 |
| `GET /api/native/runs/{id}/logs` | `items / next_cursor / total`；当前固定读取起始最多 10000 条，不提供分页查询参数 |
| `GET /api/native/runs/{id}/files/{filename:path}` | 任务目录内允许类型的图片、报告、JSON、CSV、Excel、日志等 |
| `GET /api/native/runs/{id}/sources/{index}/{filename:path}` | MATCH 任务引用的受控外部第二阶段来源文件 |
| `GET /api/native/runs/{id}/evidence-download` | 完成任务的证据 ZIP；未完成返回 409 |

阶段结果包含 `stage, revision, status, job_id, data, snapshots`。`data.workflow=native_labels` 标识新流程；第三阶段数据含实体归约结果。路径使用服务器记录的当前目录，不要写死某轮产物位置。

文件接口限制目录和扩展名，不能用于访问任意本地路径。证据 ZIP 包含任务目录内的产物，原始上传 PDF 位于相邻上传目录，不保证在 ZIP 中。

## 确认与重试

`POST /api/native/runs/{id}/stages/{stage}/approve`

`POST /api/native/runs/{id}/stages/{stage}/retry`

先读取阶段的 `revision`，再提交：

```json
{
  "revision": 1,
  "settings": {
    "reasoning_effort": "high",
    "max_task_concurrency": 10,
    "max_concurrency": 5
  },
  "reuse_saved": true,
  "reason": "本次操作说明"
}
```

示例版本仅示意，必须用实际查询值。可选字段还有 `auto_run`、`acknowledge_pending`；`settings` 还可带 `max_calls` 和服务覆盖信息。

- approve 仅推进等待确认阶段；已确认阶段重复确认不重复执行。
- retry 增加当前阶段版本，下游标记失效；旧文件与历史保留。
- failed/interrupted 且允许复用时检查已保存产物；completed 后重跑不按断点恢复处理。
- 运行/排队期间操作、旧版本操作或状态不适用返回 409。
- `acknowledge_pending` 不是删除/排除记录开关。
- 当前新流程没有修改台账的 PATCH 接口；不能把 `settings` 当作台账编辑入口。

阶段确认可能立即启动后续付费请求，重试也可能重新调用 API。

## 已有第二阶段来源与新一轮执行

`GET /api/native/sources` 返回服务器受控目录中可发现的第二阶段来源及 ID，不暴露任意路径选择。当前扫描 `tmp/label_lengths/` 与 `data/native_runs/`。

`POST /api/native/runs/match` 接收：

```json
{
  "request_id": "client-run-unique-id",
  "source_ids": ["从sources接口取得的ID"],
  "reasoning_effort": "high",
  "auto_run": true,
  "max_task_concurrency": 10,
  "max_calls": 24
}
```

服务先校验输入包与重复 PDF 视图，再创建只执行第三阶段的任务。不得将任意文件路径塞入 `source_ids`。

`POST /api/native/runs/{id}/rerun` 为 completed/failed/interrupted 任务创建新任务，JSON 包含新 `request_id`、思考强度及可选并发/预算；沿用原输入与自动/暂停策略。相同请求标识及相同创建参数可防止重复启动，参数冲突则拒绝。

`request_id` 为 1–120 位字母、数字、下划线或连字符，不包含点号或空格。它不是模型生成的钢材实体 ID。

## 状态与旧接口

常见任务状态为 `queued / running / awaiting_review / completed / failed / interrupted`；阶段另有 `approved / stale`。重启恢复标记中断，不自动恢复付费调用。

旧 `/api/jobs` 的上传、编辑、确认、重试与下载继续兼容历史流程，其模型和数据结构不同。网页将两种任务统一展示，不代表两种 API 可互换。详见 [架构与兼容边界](architecture.md)。
