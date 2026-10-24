# 第二阶段：固定标签长度识别

入口：[label_length_workflow.py:run](../app/label_length_workflow.py)。输入第一阶段 `stage_one_facts.json`，输出 `label-lengths/v1` 的逐标签台账，已接入主网页。

## 固定内容与模型职责

程序调用前检查事实包与高清图指纹。第一阶段标签 ID、规格、数量、组合倍率、当前面及已有 `view_tags` 保留，不让模型重写。每个标签是一条观察，同一视图同规格、同长度的不同标签仍保留独立数量。

模型负责端部、拼接点、尺寸链与构件的对应解释，返回原生尺寸文字 ID、系数、端点描述及依据。程序核查引用并计算数值。图上像素、标签数量或其他代理的意见不能直接变成确定长度。

轴线次序、名称和位置来自第一阶段；邻近归属仍可能有误，模型应记录疑点，不能静默改身份。原始大量矢量指令不直接传入本阶段 API；当前请求使用图像、相关原生文字与轴面关系。

## 每视图代理流程

| 角色 | 输入 | 任务 |
| --- | --- | --- |
| 网格子代理 | 自己的裁片、唯一分配的目标标签、上下文标签、裁片内原生文字与轴面排列 | 解释目标标签对应的尺寸；看不到端部/标尺时保留 UNKNOWN |
| 总代理 | 完整高清图、完整视图事实、子代理结果 | 逐 ID 汇总、结合全图纠正裁片遗漏 |
| 长度专家 | 目标记录、已有候选、完整图和扩展/合并局部图 | 处理 UNKNOWN、本地引用错误、疑点、主/子冲突与斜撑直角边 |
| 冲突仲裁 | 仍不一致的主/专家候选及图证 | 选择 MASTER / EXPERT / REVISE / UNKNOWN |

专家和仲裁默认每批最多 6 个标签，按文字方向/位置分组，范围可配置为 1–12。局部上下文会沿标签方向扩展：横向通栏、竖向通高，斜向取局部区域；重叠区域合并，最多 4 个，另始终提供完整图。这些裁片不声称已经覆盖真实构件两端。

这是有依赖、按需触发的多角色流程，**不是最多三轮整图自复核**。后续角色显式收到所需前序结果；没有共享的持久聊天记忆。每种请求最多一次格式/ID/传输层的工作流重试，失败不会无限追加角色轮次。

## 网格规则与并发

[grid_layout.py](../app/grid_layout.py) 默认参数：

| 参数 | 值 | 作用 |
| --- | ---: | --- |
| 触发边长 | 2200 px | 仅超过阈值的方向切分 |
| 目标核心边长 | 1800 px | 计算行列数后均分原图 |
| 相邻重叠 | 240 px | 每侧约 120 px，图边裁止 |
| 最大网格数 | 64 | 超限报错，不缩图或丢弃区域 |

一方向长只切该方向，两方向长则二维切图；当前不按钢材数量阈值切图。标签中心只分配到一个核心区，重叠区标签只提供上下文。空目标网格不调用 API，裁片边界不能当构件端点。

全部 PDF 的第一阶段先完成。第二阶段各文件协调器共享 task 许可，默认最多 10 个视图同时工作；每视图拥有独立的 5 请求额度，网格/主/专家/仲裁/重试共用自己的额度。详见 [配置](configuration.md)。结果由协调器落账；单个文件失败不抹掉其他文件的已保存结果。

## 模型返回与本地计算

非仲裁角色回复形状如下，所有 ID 和文字仅示意：

```json
{
  "members": [{
    "label_id": "输入中的目标标签ID",
    "kind": "STRAIGHT",
    "dimensions": [[{"text_id": "原生尺寸文字ID", "factor": 1}]],
    "endpoints": ["具体起点或拼接位置", "具体终点或拼接位置"],
    "basis": "构件位置、走向，以及尺寸与端点的对应依据",
    "issue": ""
  }]
}
```

| kind | dimensions | 本地计算 |
| --- | --- | --- |
| `STRAIGHT` | 一组表达式 | 原生数值乘系数后求和 |
| `BRACE` | 两组表达式，依次为所在平面的水平/竖向直角边 | 两边平方和开方 |
| `UNKNOWN` | 空数组 | 长度为空，保留标签和数量 |

一组表达式有 1–16 项。引用必须存在、恰有一个可解析毫米值；系数非零，范围 -1000 至 1000，所得直段/直角边必须为正。非 UNKNOWN 需要两个不同非空端点与依据。米/毫米已在文字事实中处理，模型不能再重复乘 1000。

[label_length_contracts.py](../app/label_length_contracts.py) 用 Decimal 完成求和与勾股，按 ROUND_HALF_UP 保留两位小数。模型不返回权威 `length_mm`。非 1 系数的工程依据由模型说明；本地能验证数值来自原文，不能自动证明系数、端点或尺寸归属正确。

遇到 OF SPLICE，Prompt 要求追踪实际引线/参考线、区分整跨与单段；不能因为相交、经过轴线或跨标高就切段，也不能无证据把远处拼接套到其他构件。

## 失败、状态与交付

缺少端点/尺寸或没有可用答案时保留 UNKNOWN。专家失败优先保留可用的已有候选；数值冲突回退标为 `CONFLICT_FALLBACK`，疑点可用结果为 `WITH_ISSUE`，常规推导为 `MODEL_DERIVED`。这些都不是人工确认。

输出包括：

- `stage_two.json`：固定标签、长度、直角边、表达式、端点、依据、疑点、候选与采用角色。
- `stage_one_facts.snapshot.json` 与图像副本：第三阶段的固定来源。
- `members.csv`、`stage_two.xlsx`、`index.html`：逐标签及逐视图汇总；不跨视图去重，不展开组合。
- `grids/`、`runs/`、`responses/`、`logs/`：网格分配、提示词、原始回答、请求签名、本地校验、耗时及用量。

输入/图片/Prompt/schema/请求参数一致且契约通过时，显式恢复可以复用匹配的模型响应。不会把手工改过的台账当模型回答。

## Prompt 路径

- [label_length_common.md](../app/prompts/label_length_common.md)：事实边界、端点、拼接、尺寸链及表达式。
- [label_length_child.md](../app/prompts/label_length_child.md)：局部目标与上下文。
- [label_length_master.md](../app/prompts/label_length_master.md)：全图汇总。
- [label_length_expert.md](../app/prompts/label_length_expert.md)：按需补证与斜撑核验。
- [label_length_arbiter.md](../app/prompts/label_length_arbiter.md)：冲突选择与修订。

JSON schema 同时由 Python 契约生成；修改 Prompt 的返回格式时须一起检查契约，不能只改文字说明。

## 独立运行

先只准备输入与裁片，不调用 API：

```powershell
.\.venv\Scripts\python.exe tools/test_label_lengths.py --facts tmp/pdf_label_view_tags/my-run/stage_one_facts.json --output tmp/label_lengths/my-run
```

使用同一准备目录，显式调用配置的 API：

```powershell
.\.venv\Scripts\python.exe tools/test_label_lengths.py --facts tmp/pdf_label_view_tags/my-run/stage_one_facts.json --output tmp/label_lengths/my-run --api --resume
```

可重复 `--view <view_id>` 筛选视图；`--reasoning-effort low|high|max` 默认 high；`--max-concurrency` 控制每视图请求数。CLI task 数使用环境变量。下一步见 [第三阶段](cross-view-entities.md)；准确率单独按 [验收口径](validation.md) 评价。
