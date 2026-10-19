# 按阶段代码审计

本次清理基于 `e362ac8` 后的实际调用链。不删除任务、PDF、SQLite、模型响应、历史图片或已导出台账。

| 范围 | 当前使用 | 删除依据 |
| --- | --- | --- |
| 第一阶段 | `stage_one.prepare` → 页面用途 → 建造视图目录 → `freeze_images` | 删除无当前调用入口的 `catalog_only`、`discover`、`view_locator`、文件名分类、旧原生目录生成器及其专属测试；页面用途由 Agent 判断。 |
| 第二阶段 | `recognize_views` → 图像和目录 → `validate_rows` / `calculate_length` → 本地覆盖检查 | 删除迭代提示词、修订回归比较、自动升档/格式重试及轮次选优元数据；保留单次并发识图、来源面补充、斜撑本地计算。覆盖诊断移到 `coverage.py`。 |
| 第三阶段 | `propose_groups` → `reconcile` → `expand_confirmed_groups` → 导出 | 删除旧 XYZ 实体匹配、坐标合并、旧台账流程和旧工作簿写入器；保留 AI 提案、分配守恒、待确认项及组合展开。 |
| 数据对象 | `View`、`StageOne`、`StageTwo`、模型响应和 `GroupStage` | 删除坐标/标高约束、边界、几何长度候选和旧实体类；持久化目录显式编辑时可忽略旧坐标元数据，模型返回契约继续严格校验。 |
| 配置与规格 | `settings._api_settings`、`profiles.section` | 从旧 `llm_runner.py` 和 `geometry.py` 提取唯一仍用的配置/规格函数，删除其他旧算法、`reconciliation.py`、`workbook.py`。 |
| 界面和 API | 目录/台账/组分配编辑、图片与日志 | 删除 XYZ 编辑器、手工端点标记、旧几何匹配、原生目录展示及专属 `/evidence` 查询；修改理由继续通过版本快照保存。 |
| 工具 | `run_full_test.py` + 原版 `judge.py` | 替代旧两阶段/固定四文件驱动及旧返回格式对比工具；保留当前渲染、并发、分组和独立参考评估工具。 |

当前运行时仅有四份提示词。旧流程专属测试删除，现行版本控制、重启恢复、失败重试、图片像素、组合数量、标签数按 `member_cnt` 累加、斜撑精度、并发和自动导出测试保留。

`tools/judge.py` 为用户脚本逐字副本。验收在新任务使用源任务的全部七份 PDF、默认 high、`.env` 并发，不复用旧响应，不在请求中传入验收脚本或 MTO。模型调用和导出全部结束后才评分，原始差异 CSV 保留，评分不回写模型结果。
