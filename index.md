# 量化交易系统开发学习 Wiki

本 Wiki 用来逐步整理量化交易系统开发知识。先从 VeighNa（vn.py）生态、交易系统分层和 CTP 连接开始，再扩展到策略引擎与因子研究。

## 学习路线

1. 了解 [VeighNa 平台与交易系统架构](pages/veighna-platform-and-architecture.md)
2. 准备开发环境，了解 [Agent 使用安全](pages/agent-safety.md)
3. 配置 [SimNow 与 CTP 最小连接 Demo](pages/simnow-ctp-demo.md)
4. 学习 [策略引擎的职责](pages/strategy-engine.md)
5. 学习 [截面因子与 vn.py Alpha](pages/cross-sectional-factors.md)
6. 顺着源码阅读 [vn.py 核心框架](pages/vnpy-core-source-guide.md)、[CTP Gateway](pages/vnpy-ctp-source-guide.md) 和 [CTA 策略模块](pages/vnpy-ctastrategy-source-guide.md)
7. 用 [三模块端到端调用链](pages/source-integration-walkthrough.md) 串起一次行情到策略下单的流程
8. 运行 [vnpy_ctp 底层 API 行情 Demo](demos/README.md)
9. 按[交易系统分层学习指南](pages/veighna-system-learning-guide.md)理解标准化、引擎和策略应用之间的闭环
10. 查看 [CTP 阈值下单 Demo](demos/README.md#ctp-单次阈值下单-demo) 和 [开盘跳空动量因子实验](factor_results/opening_gap_momentum_20261005/README.md)

## 作业提交

- [Issue #2 提交对照说明](提交说明.md)：逐项标注已完成内容、范围限制和提交前建议。
- [开盘跳空动量 Top20 与实验记录](factor_results/opening_gap_momentum_20261005/README.md)

## Wiki 维护

- [维护约定与 LLM 工作流](llm-wiki.md)：如何从原始资料整理、链接和维护知识页。
- [主题页模板](templates/topic.md)：新增主题时可复制此模板。
- [变更日志](log.md)：记录知识库结构和内容的主要变化。

## 原始资料

- `-VeighNa.pdf`：课程讲义《当代量化交易系统的原理与实现》，讲者陈晓优，2026-09-18。当前摘要页：[VeighNa 平台与交易系统架构](pages/veighna-platform-and-architecture.md)。
- `作业.doc`：目录中现有 Word 文件，经文件元数据检查为 1 页、0 字；暂未从中提取到学习内容。
- `raw/`：Gitee 克隆的三个源码仓库；版本和提交号见 [`raw/README.md`](raw/README.md)。源码解读：[核心框架](pages/vnpy-core-source-guide.md)、[CTP](pages/vnpy-ctp-source-guide.md)、[CTA](pages/vnpy-ctastrategy-source-guide.md)。

## 状态

这是初始知识库。页面内容来自课程 PDF 和本项目发起时提供的补充说明；后续应优先把代码实践、官方文档和验证结果补入对应主题页，并注明来源与适用版本。
