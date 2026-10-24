# 文档目录

本文档对应 2026-10-24 的代码：8120 原任务页面、新上传使用的固定标签三阶段流程，以及保留的旧任务兼容链。当前代码行为优先于历史实验记录。

## 使用与维护

| 文档 | 内容 |
| --- | --- |
| [项目说明](../README.md) | 功能、安装、启动与最近已完成的严格指标 |
| [网页操作](native-workflow-web.md) | 上传、阶段展示、暂停确认、重试、下载及常见状态 |
| [配置](configuration.md) | 环境变量、两层并发、思考强度、预算与适用范围 |
| [架构](architecture.md) | 模块边界、阶段契约、数据存储及旧接口兼容 |
| [接口](api.md) | 当前上传、状态、阶段操作与文件接口 |
| [测试与指标](validation.md) | 本地回归、真实 API 验收、评分定义与基准限制 |
| [开发交接](current-workflow.md) | 各阶段入口、交付、并发与运行状态核对要求 |
| [更新日志](../CHANGELOG.md) | 版本变更；历史成绩保留当时口径 |

## 当前算法

| 阶段 | 文档 | 核心约束 |
| --- | --- | --- |
| 一 | [原生标签、轴面与高清裁切](pdf-label-tags-experiment.md) | AI 定位，程序建标签事实包；文件名保留历史命名，功能已接入主网页 |
| 二 | [固定标签长度识别](label-constrained-lengths.md) | 标签不增删，模型解释尺寸对应，本地算术 |
| 三 | [跨视图匹配与去重](cross-view-entities.md) | 每条观察归属一次，只选择已有长度，不自由重算 |

## 历史兼容与实验记录

以下文档保留开发过程，不能用其中的端口、分支状态、旧 Prompt、并发限制或成绩替代当前说明。

- [完整 key 去重](exact-key-dedup.md)：旧 `/api/jobs` 链仍使用的兼容规则。
- [自动长度仲裁](automatic-length-arbitration.md)、[早期长度专家验证](length-expert-validation.md)、[05 长度专家验证](length-expert-05-validation.md)。
- [第一、二阶段网格验证](stage12-grid-validation.md)、[旧全流程经济模式验证](full-pipeline-economy-validation.md)。
- [标签着色实验](label-color-experiment.md)、[04 着色实验结果](label-color-04-results.md)。当前固定标签上传链使用原始高清 PNG，未接通旧着色选项。
- [早期重构审计](refactor-audit.md)、[早期重构结果](refactor-results.md)。

验收 PDF、人工参考表、模型原始回复和任务输出属于本地数据，不随仓库发布。文档示例中的路径和 ID 应换成自己的实际输出，不包含测试答案。
