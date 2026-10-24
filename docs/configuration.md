# 配置说明

在仓库根目录复制 [.env.example](../.env.example) 为 `.env`，已有文件不要覆盖。`.env`、任务和日志均被 Git 忽略。服务从项目目录加载配置；修改环境变量后应在任务结束后重启同一个 8120 服务。

## 当前固定标签流程

| 环境变量 | 默认值 | 生效范围 |
| --- | --- | --- |
| `LLM_BASE_URL` | 无 | OpenAI 兼容 API 地址 |
| `LLM_API_KEY` | 无 | API 凭据 |
| `LLM_MODEL` | 无 | 服务中的模型标识；需接受图片及结构化 JSON 请求 |
| `LLM_TASK_CONCURRENCY` | 10 | 第一阶段文件、第二阶段视图、第三阶段匹配组的 task 上限 |
| `LLM_MAX_CONCURRENCY` | 5 | 第二阶段每个视图内部的请求上限 |
| `LABEL_LENGTH_REVIEW_BATCH_SIZE` | 6 | 第二阶段专家/仲裁每批标签数，整数 1–12 |
| `ENTITY_MAX_CALLS` | 24 | 第三阶段本次执行的请求总预算，包括格式/传输重试 |

并发值须为正整数，设为 1 可串行；10 和 5 是默认值，不是代码写死的最大可选值。网页填写的 task/request 设置优先于环境变量；留空使用环境配置。

例如 task=10、每视图=5 表示最多 10 个视图同时工作，各自最多 5 个请求。它不是全局 5 请求，也不保证始终有 50 个请求。第一、二、三阶段不重叠执行；第三阶段使用 task 上限，并不使用第二阶段的“每视图 5”池。

第二阶段没有整视图固定两次调用的费用上限：网格数、专家批次、冲突数和失败重试都会增加调用。第三阶段预算耗尽时，尚未解决的匹配组回退为独立观察并导出，可能保留重复。预算是每次执行的上限，不是任务历史所有重跑的累计限额。

## 网页与 API 覆盖

| 网页/API 含义 | API 字段 | 说明 |
| --- | --- | --- |
| 主 task 并发数 | `max_task_concurrency` | 覆盖 `LLM_TASK_CONCURRENCY` |
| 每视图代理请求并发数 | `max_concurrency` | 覆盖 `LLM_MAX_CONCURRENCY` |
| 思考强度 | `reasoning_effort` | `low / high / max`，默认 `high` |
| 第三阶段调用预算 | `max_calls` | API 可覆盖；当前网页没有独立预算输入框 |
| 服务与凭据 | `base_url / api_key / model` | 网页覆盖值仅在服务内存保留 |

思考强度不是通用服务保证。[pipeline.py 的 request_options](../app/pipeline.py) 对支持的模型标识设置 thinking 和输出预算；其他模型可能不发送这些选项。不要把页面选择 `high` 等同于所有兼容服务都执行同样强度。当前代码没有 `LLM_REASONING_EFFORT` 环境变量入口。

原页面保留的“钢材名/轴线名着色图（实验）”属于旧功能；当前新上传处理器不读取该选项，不会改变新流程的识图输入。

## 仅旧链路/实验使用的配置

| 环境变量 | 默认值 | 边界 |
| --- | --- | --- |
| `LENGTH_REVIEW_MODE` | `economy` | 旧长度仲裁流程的 economy/full 模式 |
| `LENGTH_REVIEW_MAX_CALLS` | 2 | 旧长度角色预算，不包含网格/总代理 |

它们仍在示例配置中，但**不是固定标签第二阶段的开关或费用上限**。当前算法的专家与仲裁使用 `label_length_*` 模块和 Prompt。

## CLI 参数差异

- [第一阶段工具](../tools/test_pdf_label_tags.py) 默认就会调用 API；`--reuse-localization` 用于校验并复用已保存的定位结果。
- [第二阶段工具](../tools/test_label_lengths.py) 默认只准备输入，加 `--api` 才调用；`--max-concurrency` 控制每视图请求数，`--review-batch-size` 控制专家批次。task 数通过环境变量设置。
- [第三阶段工具](../tools/test_entity_reconciliation.py) 默认本地准备/规则归约，加 `--api` 才调用；`--max-calls` 控制预算。当前工具虽接受 `--max-concurrency`，但第三阶段实际线程池读取 task 配置；请用 `LLM_TASK_CONCURRENCY` 或网页/API 的 `max_task_concurrency` 调整。

输出目录已有输入清单时，只有显式 `--resume` 且输入/Prompt/契约等检查一致才能复用；否则换新目录。改变 Prompt 或模型请求参数不能保证命中缓存。

## 服务核对

```powershell
Invoke-RestMethod http://127.0.0.1:8120/api/native/capabilities
```

当前接口应包含 `ui=original-stage-page`、`concurrency_model=per-view-v1`、`stage_controls=true`、`readonly_ledger=true`。并发数显示服务的环境默认值，不一定等于某个任务的覆盖值。

若接口 404 或旧地址跳转失败，应先核对启动目录和运行版本。不要另开端口解决旧进程问题；也不要在付费请求执行中直接重启。
