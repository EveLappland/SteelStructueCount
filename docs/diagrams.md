# 当前项目架构图与流程图

核对日期：2026-10-26；基于 `e8a3458` 的执行代码。图中并发数为默认值，可由环境变量或任务设置覆盖。模块详情见 [架构](architecture.md)，契约见各阶段文档。

## 架构图

实线表示主要调用或数据交付，虚线表示共享支撑能力。旧任务兼容链继续存在，其数据结构与固定标签新链独立。

```mermaid
flowchart TB
  UI["原任务网页 · 8120<br/>index.html / app.js / fixed_stage.js"]
  HTTP["FastAPI · main.py"]
  UI --> HTTP
  subgraph Native["当前固定标签主服务"]
    WEB["native_web.py · NativeRuns<br/>上传、任务排队、阶段控制、历史与重试"]
    ADAPTER["native_stages.py<br/>阶段结果展示适配"]
    S1["第一阶段 · label_catalog<br/>定位、原生解析、标签事实包"]
    S2["第二阶段 · label_length_workflow<br/>网格、总代理、专家、仲裁、本地计算"]
    S3["第三阶段 · entity_workflow / entity_reduction<br/>跨视图匹配、已有长度消歧、守恒检查"]
    EXPORT["entity_report.py<br/>Excel、JSON 与报告"]
    WEB --> S1 --> S2 --> S3 --> EXPORT
    WEB --> ADAPTER
  end
  HTTP --> WEB
  subgraph Support["共享支撑"]
    API["OpenAI 兼容 API<br/>pipeline.model_call / settings / prompts"]
    SLOTS["task_slots.py<br/>task 默认 10；第二阶段每视图请求默认 5"]
    PDF["PyMuPDF · native_pdf / drawing_input<br/>原生提取与渲染共享锁"]
    AUDIT["audit.py<br/>请求、响应、校验与事件日志"]
  end
  S1 -.-> API
  S2 -.-> API
  S3 -.-> API
  WEB -.-> SLOTS
  S2 -.-> SLOTS
  S3 -.-> SLOTS
  S1 -.-> PDF
  S2 -.-> PDF
  WEB -.-> AUDIT
  S1 -.-> AUDIT
  S2 -.-> AUDIT
  S3 -.-> AUDIT
  DB[("native_runs.sqlite3<br/>状态、版本与历史")]
  FILES[("data/native_runs/<id><br/>图片、事实包、观察、实体与导出")]
  WEB --> DB
  S1 --> FILES
  S2 --> FILES
  EXPORT --> FILES
  ADAPTER --> FILES
  subgraph Legacy["旧任务兼容链"]
    OLD["/api/jobs · pipeline.execute<br/>旧阶段执行、编辑与重试"]
    OLDDB[("jobs_v2.sqlite3 / data/jobs")]
    OLD --> OLDDB
  end
  HTTP --> OLD
```

第一阶段交付 `stage_one_facts.json` 与 300 DPI 图像；第二阶段交付 `stage_two.json` 的固定标签长度台账；第三阶段交付 `stage_three.json`、单根汇总与证据导出。各阶段产物和历史独立保存，两个任务数据库不自动迁移或混用。

## 流程图

主路径为新 PDF 上传后自动完成三阶段。阶段间有交付屏障；启用暂停时，须在相应阶段完成后确认才继续。第二阶段的候选均经原生文字引用与本地算术校验。

```mermaid
flowchart TD
  UP["上传 PDF 与本次设置"] --> QUEUE["保存源文件、创建任务、排队"]
  QUEUE --> S1["第一阶段：默认并发 10 个文件<br/>每个文件逐页处理"]
  S1 --> LOC["AI 定位 PLAN / ELEVATION<br/>自动忽略说明、索引、概览和节点详图"]
  LOC --> FACTS["本地提取标签、轴面与尺寸文字<br/>生成事实指纹与 300 DPI 图片<br/>无目标页不生成视图裁片"]
  FACTS --> B1["全部文件第一阶段交付<br/>如设置暂停，则等待确认"]
  B1 --> S2["第二阶段：共享 10 个视图 task 名额<br/>每视图独立最多 5 个代理请求"]
  S2 --> CHILD["重叠网格子代理识图"]
  CHILD --> MASTER["总代理整合全视图尺寸解释"]
  MASTER --> CHECK{"存在未知、斜撑、问题<br/>或候选解释不一致？"}
  CHECK -- 是 --> EXPERT["长度专家按需分批核验"]
  EXPERT --> CONFLICT{"与原候选存在冲突？"}
  CONFLICT -- 是 --> ARBITER["冲突仲裁"]
  CHECK -- 否 --> LEDGER["固定标签观察台账<br/>保留身份、规格、数量与所属面"]
  CONFLICT -- 否 --> LEDGER
  ARBITER --> LEDGER
  MASTER -. "请求或回答无效：最多一次工作流重试" .-> RETRY["仍失败则使用有效已有候选<br/>无法确定时保留 UNKNOWN 与原因"]
  RETRY --> LEDGER
  LEDGER --> B2["全部文件第二阶段交付<br/>如设置暂停，则等待确认"]
  B2 --> CANDIDATE["第三阶段：按所属面与规格构造候选<br/>单面或无候选观察直接保留"]
  CANDIDATE --> RULE{"可本地同质数量归约？"}
  RULE -- 是 --> MERGE["生成匹配实体"]
  RULE -- 否 --> LIMIT{"组不超过 32 条<br/>且请求预算充足？"}
  LIMIT -- 是 --> MATCH["AI 匹配：默认 10 个组 task<br/>总预算默认 24 次，包含重试<br/>只选第二阶段已有长度"]
  MATCH --> VALID{"来源、分配与数量校验通过？"}
  VALID -- 是 --> MERGE
  VALID -- "失败，最多重试一次后仍无效" --> FALLBACK["FALLBACK：独立保留来源观察"]
  LIMIT -- 否 --> FALLBACK
  MERGE --> CONSERVE["每条观察恰好归属一次<br/>同视图独立标签不可合并<br/>数量守恒检查"]
  FALLBACK --> CONSERVE
  CONSERVE --> OUT["组合只展开一次<br/>导出 Excel、JSON、报告与证据包<br/>如设置暂停，则等待第三阶段确认"]
  OUT --> DONE["任务完成<br/>UNKNOWN 与回退记录仍保留"]
```

图中重试框以总代理为示例：第二阶段子代理、总代理、专家和仲裁请求均适用格式/传输重试规则；第三阶段重试还受总预算约束。原生提取与渲染加锁，API 等待不占该锁。

失败/中断后须显式重试，可复用签名校验通过的已有响应；已完成任务从第二阶段重跑时保留第一阶段，使下游当前结果失效并保留历史。服务重启不自动恢复付费运行。

completed 表示流程结束与结果可导出；请求失败可能转换为 UNKNOWN 或 FALLBACK。第一阶段目前只有 AI 自动过滤，网页没有手动排除页面、视图或规格的入口。输出为图示名义长度，非加工下料长度；UNKNOWN 为零与数量守恒都不能证明工程准确率。
