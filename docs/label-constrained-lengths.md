# 固定标签第二阶段

开发分支：`feature/label-constrained-lengths`，基于第一阶段交付 `21288b7`。
这是可独立运行的第一/二阶段新链路，不自动改写现有网页任务，也未接入第三阶段。

## 输入与边界

输入是第一阶段生成的 `stage_one_facts.json`，格式为 `native-label-facts/v1`。
`app.label_facts.load()` 在调用API前校验事实包及300DPI原图指纹。
标签ID、规格、计数（每标签一根/一个组合）、已有view_tags固定；轴线位置和排列由程序提供。
AXIS_PROPOSED仍是程序邻近关系判断，不表示构件端点、拼接或长度已经确认。
模型不重复生成规格/数量/轴线目录，不返回view_tags。

原生 `text_facts` 提供文字ID、原文、图上位置及能严格解析的尺寸/标高值（毫米）。
尺寸起止、归属哪根构件、拼接位置仍由模型解释图片；算术和引用校验通过不等于工程准确率通过。
没有输入人工台账或评分基准的参数，也不读取验收目录。

## 调度

一个全局队列和线程池调度所有视图的角色，共享 `.env` 的 `LLM_MAX_CONCURRENCY`（默认5，可由命令行覆盖）。
只有HTTP请求在工作线程执行。裁切、共享状态、候选合入及checkpoint由协调线程串行处理。

1. 程序复用现有 `grid_layout.py` 切图，按标签中心将每个ID分配到唯一核心区。重叠区标签提供上下文，不额外计数；空网格不调用API。
   子代理只收到当前裁片的相关原生文字及完整轴线排列，总代理/专家/仲裁保留完整视图文字；省略只供审计的长矢量ID列表，减少无关上下文和调用费用。
2. 网格子代理只为分配的ID解释端点、拼接和尺寸，不能把裁片边界当端点。越界证据不足可返回UNKNOWN。
3. 总代理看完整原图与局部结果，逐ID输出；程序补回规格、计数与原有view_tags，不能合并不同ID。
4. 专家只处理UNKNOWN、局部引用错误、子/主解释冲突、有issue的记录及斜撑两条直角边。输入包含完整图和沿构件方向扩展/合并的高清上下文，最多4个区域。
5. 专家与已有解释仍有数值/直角边冲突时才调用一次仲裁，选择MASTER、EXPERT、REVISE或UNKNOWN。没有冲突不调用仲裁。

每种请求最多一次格式/ID/传输重试，不把损坏JSON当答案模板。所有ID必须与目标一一对应。
专家或仲裁失败时保留已有行和可用候选，失败与冲突状态可见，不等待人工、不静默删行。
仲裁失败的数值回退标记 `CONFLICT_FALLBACK`；模型推导结果标记 `MODEL_DERIVED`，不伪装成人工确认。

## 结构与计算

尺寸对象仅有 `label_id, kind, dimensions, endpoints, basis, issue`。
`dimensions`是原生文字引用与系数的表达式数组：

- STRAIGHT：一组表达式，对原生尺寸进行加减/有图证的比例分配。
- BRACE：两组表达式，依次为水平和竖向直角边；本地计算平方和开方。
- UNKNOWN：空数组，台账仍保留。

每个表达式项包含 `text_id, factor`。原生目录已经将标高米值换算成毫米，不让模型重复换算。
程序拒绝不存在/非数值的文字引用、零系数、非正长度、缺少端点或维度形状错误。
使用Decimal计算加减、勾股与四舍五入，输出保留两位小数。系数的几何意义需要模型在basis提供依据，程序不声称已证实该解释。

## 运行

项目根目录PowerShell，先准备网格和提示词，不调用API：

```powershell
.\.venv\Scripts\python.exe tools/test_label_lengths.py --facts tmp/pdf_label_view_tags/pdf02/stage_one_facts.json --output tmp/label_lengths/pdf02
```

确认输入后调用当前 `.env` 的API（已有目录显式resume）：

```powershell
.\.venv\Scripts\python.exe tools/test_label_lengths.py --facts tmp/pdf_label_view_tags/pdf02/stage_one_facts.json --output tmp/label_lengths/pdf02 --api --resume
```

新目录可直接使用 `--api`；`--view <view_id>`可重复，用于测试其他视图；`--max-concurrency`覆盖并发设置；
`--reasoning-effort low|high|max`默认high，对支持该参数的配置模型生效。
resume只复用输入、图片、prompt、schema、模型参数与服务地址指纹一致的响应；不会复用修改后的旧回复。
输入JSON按键排序，避免异步子任务完成顺序变化导致总代理缓存失效；新提示词或契约发生变化仍需真实重算。

输出：`stage_two.json`、逐标签`members.csv`、`stage_two.xlsx`和`index.html`。
`runs/`保存每次输入包、固定ID网格分配、各角色prompt和本地验证结果；`responses/`保存完整真实回复与请求签名；`logs/`保存脱敏日志。
stage_two.json在执行中刷新，任何阶段失败仍包含全部原标签，缺长度记UNKNOWN而不是零。
按视图导出数量与长度汇总，未执行跨视图去重，也未展开2LF组件。

## Prompt路径

- `app/prompts/label_length_common.md`：事实边界、端部/拼接/标尺规则和尺寸表达式。
- `app/prompts/label_length_child.md`：局部网格任务。
- `app/prompts/label_length_master.md`：全图汇总。
- `app/prompts/label_length_expert.md`：按需补证和斜撑直角边核验。
- `app/prompts/label_length_arbiter.md`：只裁决仍有冲突的ID。

测试：`python -m unittest discover -s tests -p test_label_lengths.py -v`。
新增测试只检查本地几何、文件契约、来源引用与算术，不模拟API回复；真实链路验证使用配置的实际API。
