# 固定标签第三阶段：跨视图实体归约

分支 `feature/cross-view-entities`，承接 `label-lengths/v1` 第二阶段真实结果。
这是新固定标签链路的第三阶段工具与结果页，未替换旧SQLite任务接口；原有任务不会自动迁移。
程序不读取人工台账、不接收参考答案；AI请求及中间回答全部审计，无人工改结果步骤。

## 运行

```powershell
.\.venv\Scripts\python.exe tools/test_entity_reconciliation.py --stage-two tmp/label_lengths/pdf02 --stage-two tmp/label_lengths/pdf04 --output tmp/label_lengths/entities02_04 --api
```

`--stage-two`可重复，传目录或stage_two.json。每份目录需保留 `stage_one_facts.snapshot.json` 和二阶段高清原图。
不加`--api`只生成输入/提示词及规则匹配试算，未调用项目会显示PREPARED/回退独立观察；不能视为AI已裁决。
相同输出目录须显式 `--resume`，并校验输入文件指纹、prompt、schema和参数；仅请求签名相同且通过契约检查的历史响应可复用。
第二阶段JSON、图片及一阶段快照在调用前校验，第三阶段不写回它们。

思考默认high，可用 `--reasoning-effort low|high|max`；并发共享`.env LLM_MAX_CONCURRENCY`默认5，可用`--max-concurrency`覆盖。
`ENTITY_REVIEW_BATCH_SIZE=6`限制每个独立长度补证任务标签数（1～12，`--batch-size`覆盖），并按视图、文字方向分批。
`ENTITY_MAX_CALLS=24`是一次调用的总请求预算，包含一次格式/引用/传输重试（`--max-calls`覆盖）。
超过预算、单个不可拆分匹配组超过32条或两次均失败时，保留全部源观察为独立实体，明确标记FALLBACK；可能残留重复，不假装已经去重。
同一输出目录resume的预算按本次执行计算；复用的响应记录在审计中，新增付费请求数应以MODEL_REQUEST_STARTED事件为准。

## 匹配原则

1. 观察ID是 `PDF完整SHA256:label_id`；视图ID同样带来源命名空间。不同PDF局部ID重名不会覆盖；同一PDF同一视图重复输入会被拒绝。
2. `spec + view_tags + length`不是实体唯一ID。长度不作为候选门槛，UNKNOWN及不同长度可以进入同一核验组。
3. 跨视图候选要求规范规格/组合倍率一致，且双方view_tags分别包含对方当前所属面。相同标高下A/B等不同位置、同一面不同副本、仅仅对称均不能自动合并。
4. 同一视图不同钢材标签代表不同出现，不能压成一个实体。每条观察恰好归属一次；每个逐根实体在一个视图最多一条观察。
5. 无疑点且各视图标签/长度分布完全相同的组，按相同数量归约为同质数量组。例如3对3保留3个实体数量、6条来源；不虚构无法区分的逐根配对。
6. 数量或长度分布不同、UNKNOWN、原结果有疑点的关联组，交给Agent按原图端点/拼接/顺序划分实体，不能机械取最大值或最小值。
7. 无跨视图候选的疑点/未知长度及未经过专家处理的斜撑，按小批补证。已无疑点的独立观察保留二阶段长度，不额外调用模型。

图中轴线近邻关系仍是一阶段的程序提议，不代表已确认物理端点。
缺乏相互所属面标签时本版不自动添加或猜测标签来扩大匹配，保留独立观察；更复杂的空间定位仍需后续证据。

## Agent与证据

提示词：`app/prompts/entity_reconciliation.md`。
每次仅给该关联组/补证批次的观察、原始长度表达式、程序轴线关系、原生数字/轴名/拼接文字和相关原图/沿构件方向的局部图。
`image_map`明确标识每张图片来自哪个视图。不同图片的相同text_id只在对应视图范围有效。
输出只划分 observation_ids、选择 length_source_id 或 revision、解释reason；不能返回新规格/数量/所属面或直接计算的长度。

修订测量复用 `LabelLength` 契约，引用所属视图的原生文字ID、表达式系数、实际端部与依据。
本地重新计算尺寸及勾股，保留两位小数；存在文本引用不等于该尺寸属于该构件，图形对应仍是模型判断。
其他视图有长度时，只有在匹配为同一实体后才能传递，不能因多数投票或某张图简单就强行覆盖。
OF SPLICE分段需对应实际拼接基线，长度之和相等不能单独证明分段正确。

## 导出和核验

`stage_three.json`含实体、数量组、全部观察到实体的映射、原长度与采用长度、每个自动任务状态和逐视图数量守恒检查。
`stage_three.xlsx`含实体台账、跨视图观察对应、原始观察、单根汇总、展开单根台账、未知与回退、自动任务审计。
组合先匹配为一个实体/数量组，仅在单根导出时按倍率展开一次，组件ID不同，重复导出不重复累计。
UNKNOWN也保留数量，长度留空；FALLBACK即使有原长度也不是已完成匹配。
`responses/`保留真实模型JSON、schema、prompt和请求签名，`runs/`保留本地验证结果，`logs/events.jsonl`记录并发、重试、耗时及token。
`index.html`提供结果页及高清来源图，目录可由现有8127静态服务直接展示。

本地测试只验证数据契约、守恒、几何关联限制、算术和组件展开，不模拟API调用：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_entity_reconciliation.py -v
.\.venv\Scripts\python.exe tools/verify_entities.py --result tmp/label_lengths/entities02_04_final
```

工程正确率需另行离线对照，不用UNKNOWN减少量、守恒通过或填写率代替准确率。

中间真实验证结果保存在忽略的`tmp/label_lengths/entities02_04_final/`，完整02～05验证另存目录。
参考和评分只在运行完成后离线使用，不进入识别代码、prompt或API输入。
