# vnpy_ctastrategy 源码导读

## 阅读范围

沿着 `CtaTemplate`、实盘 `CtaEngine`、`BacktestingEngine` 和示例策略阅读，重点看策略状态、事件分发、下单以及回测模拟。源码分别位于 [`template.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/template.py)、[`engine.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/engine.py) 和 [`backtesting.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/backtesting.py)。

## 一句话理解

CTA 模块为单标的时序策略提供共同模板，并实现两类宿主：实盘引擎接入 MainEngine、Gateway、行情与交易事件；回测引擎按历史 Bar/Tick 顺序驱动同一类策略回调，并用简化撮合模型生成订单和成交。

## 策略模板

`CtaTemplate` 把策略逻辑约定为 `on_init`、`on_start`、`on_stop`、`on_tick`、`on_bar`、`on_order` 和 `on_trade` 等回调。策略用 `parameters` 声明可配置参数，用 `variables` 声明需展示/保存的状态，内置 `inited`、`trading` 和 `pos`。

`buy`、`sell`、`short`、`cover` 最终委托给引擎的 `send_order`。模板包含 `BarGenerator`、`ArrayManager` 的框架导出，常见策略可用它们聚合 K 线并管理指标窗口。`TargetPosTemplate` 则把“目标仓位”转换成调仓动作：有未完成委托时先撤旧单，等待后再根据 `target_pos - pos` 发新单；实盘路径会按当前净持仓选择平仓或开仓方向。

## 实盘引擎的生命周期和事件

1. `CtaEngine.init_engine()` 初始化数据服务，加载策略类、配置和状态文件，并注册行情、委托和成交事件。
2. 创建策略时校验类名和 `vt_symbol`，实例加入策略表及 `vt_symbol -> strategies` 索引。
3. 初始化任务进入单线程执行器，调用 `on_init`、恢复持久化变量、订阅合约行情并更新初始化状态。
4. 启动时先调用 `on_start`，再置 `trading=True`；停止时调用 `on_stop`、撤销策略活动委托并同步状态。
5. Tick 按 `vt_symbol` 分发；订单和成交则通过 `vt_orderid -> strategy` 找回所属策略。成交 ID 去重后，先更新 `strategy.pos`，再触发 `on_trade`。
6. 下单前按合约最小价位/数量单位取整；通过 MainEngine 的 offset converter 转换开平方向，再交给 Gateway。停止单若柜台支持则走服务器，否则由 CTA 在本地监测 Tick 触发后转为限价单。

## 回测引擎的核心假设

- `BacktestingEngine` 从数据库加载历史 K 线或 Tick，依时间顺序调用策略回调。
- 每根 Bar/Tick 到来时先检查已有活动限价单和停止单，再调用策略的 `on_bar`/`on_tick`，最后更新当日收盘价。
- Bar 模式用 OHLC 价格判断是否穿价；Tick 模式参考买一/卖一。满足条件时回测引擎生成订单、成交，并更新策略仓位。
- 日结模块根据持仓、合约乘数、手续费率和滑点计算逐日盈亏，再汇总绩效统计。

因此，回测撮合是可重复的规则模型，不是交易所队列仿真。Bar 内部先后顺序、成交概率、排队位置、冲击成本、涨跌停流动性等现实因素不会自然出现在结果里。回测数据、费用、滑点和撮合假设都应纳入策略评估。

## 实盘与回测不是完全相同的宿主

相同模板能复用策略回调，但引擎实现不同。例如实盘使用事件流、订单状态回报和 Gateway offset converter；回测使用本地限价/停止单容器和确定性撮合。参数 `lock`、`net` 会由实盘调用链传递给 offset converter，但当前检查到的 `BacktestingEngine.send_order()` 接收这两个参数后没有使用它们。需要研究锁仓、净持仓或平仓拆分时，必须同时对照两套实现，不能只看策略模板的统一签名。

## 一个容易忽略的异常路径

实盘 `call_strategy_func()` 捕获策略回调异常后会把 `trading`、`inited` 置为 `False` 并记录堆栈。但 `_init_strategy()` 调用 `on_init()` 后仍会继续恢复变量、尝试订阅，并在末尾设置 `strategy.inited = True`。因此初始化回调抛异常后的最终状态应结合这段后续逻辑理解，不能只凭异常处理函数推断。

## 建议阅读顺序

1. [`base.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/base.py)：状态枚举、实盘/回测类型、StopOrder
2. [`template.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/template.py)：策略回调、下单封装、目标仓位模板
3. [`engine.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/engine.py)：事件注册、生命周期、下单/撤单、策略加载
4. [`backtesting.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/backtesting.py)：数据回放、撮合和盈亏计算
5. [`strategies/double_ma_strategy.py`](../raw/vnpy_ctastrategy/vnpy_ctastrategy/strategies/double_ma_strategy.py)：用具体策略验证模板回调如何组合

## 来源与版本

- 源码：[`raw/vnpy_ctastrategy/`](../raw/vnpy_ctastrategy/)，包版本 1.4.1，提交 `5da04d953107c1d54385ffd2aeafd43d8fba0a16`。
- 对照：[原始仓库清单](../raw/README.md)、[策略引擎概念页](strategy-engine.md)及[核心框架源码导读](vnpy-core-source-guide.md)。
