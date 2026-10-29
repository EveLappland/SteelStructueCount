# 第三阶段：跨视图匹配、去重与消歧

入口：[entity_workflow.py:run](../app/entity_workflow.py)，规则与回复校验见 [entity_reduction.py](../app/entity_reduction.py)。当前算法标识 `cross-view-entities/v2-matching-only`，输出格式 `cross-view-entities/v2`。

本阶段不自由重算，不重新切段，不新增标签或尺寸表达式；它将第二阶段观察归约为实体，并在已有有效长度之间选择。当前已接入主网页，旧 v1 自由重算结果保留，不自动迁移。

## 输入与候选关系

读取全部 `stage_two.json`、第一阶段快照和高清图。调用前检查标签集合、规格、计数、所属面、图片指纹及已知长度的原生引用/算术。不同 PDF 的局部标签 ID 通过 `PDF完整SHA256:label_id` 加命名空间；重复输入同一 PDF 视图会被拒绝。

两个观察成为跨视图候选需同时满足：

1. 来自不同视图、不同当前所属面。
2. 双方均有附加面标签；规范规格和组合倍率一致。
3. A 的当前面在 B 的 `view_tags` 中，B 的当前面也在 A 的 `view_tags` 中。

长度不是候选门槛，因此已知/UNKNOWN、不同长度可以核验对应。反之，仅仅规格和长度相同不证明重复。当前程序不为建立候选而自由补面标签。

仅有一个 `view_tag` 的观察直接独立保留；其他没有跨视图候选的观察也保留，不调用模型。不同标高平面若没有实际互属关系，不会仅因同规格同长度被去重。

## 归约规则

候选关系形成关联组。对于所有长度已知、无疑点和本地错误，且各视图的“完整标签集合＋长度”数量分布一致、对应也满足候选关系的组，程序按同质数量组归约。例如各视图均观察到 3 根，则保留 3 个数量，不虚构无法区分的逐根配对。

其余有关联的组交给匹配 Agent。输入包含观察 ID、规格/面名、已有长度、第二阶段 `endpoints / basis / issue / review_notes`、轴面关系以及有来源标识的原图和局部图。

Prompt：[entity_reconciliation.md](../app/prompts/entity_reconciliation.md)。回复只划分来源并选长度来源：

```json
{
  "entities": [{
    "observation_ids": ["本次观察ID"],
    "length_source_id": null,
    "reason": "对应或独立保留的依据"
  }]
}
```

程序强制：

- 本次每条观察恰好归属一次，不得使用目录外 ID。
- 一个逐根实体不能包含同一视图的两个标签；同 key 的独立构件不能被压成一根。
- 合并实体内部的跨视图关系须满足允许匹配约束。
- `length_source_id` 必须属于该实体，且已有有效第二阶段测量。
- 独立观察保留原长度；全部来源未知则仍 UNKNOWN；不能合并后抹掉已有长度。
- 不接受新长度、表达式、端点、规格、数量或面标签字段。

UNKNOWN 只有在与另一条已有长度的观察匹配为同一实体后，才能继承其长度。第三阶段不负责从图上重新求解没有任何已知来源的长度。

## 并发、预算与回退

匹配组线程池使用 `LLM_TASK_CONCURRENCY`，默认 10。请求预算 `ENTITY_MAX_CALLS` 默认 24，包含一次工作流格式/传输重试；第二阶段每视图 5 请求的配置不控制这里。

关联组超过 32 条、预算耗尽或请求/契约检查失败时，回退为独立观察并记录 FALLBACK。保留数量可以保证流程导出，但可能留下重复；不能把回退当成匹配已确认。单条观察既有长度仍保留，未知仍未知。

预算按本次执行计算；恢复时实际新增付费量以事件日志为准，不应把缓存复用也算成新请求。

## 输出与单根数量

先得到实体/同质数量组，再按 `member_cnt × component_multiplier` 展开单根。组合角钢只展开一次，组件有独立 ID。

| 产物 | 内容 |
| --- | --- |
| `stage_three.json` | 实体、来源对应、采用测量、自动任务状态、守恒检查 |
| `components.json`、`quantity_summary.json`、`entities.csv` | 展开单根及规格长度数量汇总 |
| `observations.json`、`source_views.json`、`views/` | 原始观察和来源高清图 |
| `stage_three.xlsx` | 实体、单根汇总、展开台账、匹配对应、原始观察、审计、未知与回退 |
| `index.html`、`verification.json` | 可读报告及本地一致性检查 |
| `responses/`、`runs/`、`logs/` | 模型回答、提示词、请求签名、校验及事件 |

Excel 的 `Quantity Length Summary` 为评分兼容表，列为“规格、名义长度 mm、单根数量、总长度 mm”。UNKNOWN 保留数量、长度为空，不作为正确匹配。

新增“单根材料清单”子表，仅含 `PROFILE / MATERIAL / LENGTH(mm)`。每根展开单件独占一行，按 `member_cnt × component_multiplier` 展开，角钢使用去掉 `2LF-` 等组合前缀后的单件规格；不会再次乘组合倍率。长度为数值、显示两位小数，UNKNOWN 留空且保留该行。当前流程没有逐根材质来源，`MATERIAL` 留空，不从离线参考表或规格名称推断。

守恒检查验证全部观察恰好分配、每视图数量和单根展开一致；来源检查验证长度取自对应观察。它们不证明跨视图对应在工程上正确。

## 独立运行

```powershell
.\.venv\Scripts\python.exe tools/test_entity_reconciliation.py --stage-two tmp/label_lengths/run-a --stage-two tmp/label_lengths/run-b --output tmp/entities/my-run --api
```

`--stage-two` 可重复，传目录或 JSON；保留输入目录中的快照与图片。不加 `--api` 只执行准备、本地规则与回退，不能称为 AI 已核验。`--resume` 要求来源、参数与 Prompt 等一致。

预算用 `--max-calls`；task 并发用环境变量或网页/API 的 `max_task_concurrency`。当前 CLI 的 `--max-concurrency` 不控制第三阶段 task 池，详见 [配置说明](configuration.md)。

本地复核不调用 API，但会生成验证记录：

```powershell
.\.venv\Scripts\python.exe tools/verify_entities.py --result tmp/entities/my-run
```

准确率另按 [测试与指标](validation.md) 离线计算；不得将人工基准送入匹配提示词或按参考总数删行。
