# vn.py 核心框架源码导读

## 阅读范围

本页沿着事件分发、标准化交易对象、Gateway 抽象、MainEngine 路由和 OMS 缓存这条主路径阅读。源码位于 [`raw/vnpy/vnpy/`](../raw/vnpy/vnpy/)。

## 一句话理解

`vnpy` 核心框架负责定义交易系统通用语言和运行骨架：Gateway 把不同柜台协议翻译成统一数据对象和事件，MainEngine 提供统一调用入口，EventEngine 分发异步事件，OMS 保存最新状态并提供查询。

## 关键组成

### 1. EventEngine：按事件类型分发

入口在 [`event/engine.py`](../raw/vnpy/vnpy/event/engine.py)。`Event` 只有事件类型和数据载荷；`EventEngine` 用队列接收事件，工作线程逐个取出，先调用该类型的 handler，再调用通用 handler。另一个线程按固定间隔放入 `eTimer`。

这意味着事件 handler 在同一个消费线程中顺序执行。某个 handler 执行很慢，就会拖慢后续事件；跨线程传递的对象也应视为只读，避免生产者继续修改它。

### 2. 标准化对象与事件键

[`trader/object.py`](../raw/vnpy/vnpy/trader/object.py) 用 dataclass 定义 `TickData`、`BarData`、`OrderData`、`TradeData`、`PositionData`、`AccountData` 和 `ContractData` 等。对象在构造时生成 `vt_symbol`、`vt_orderid` 等跨接口标识；状态、方向、开平等枚举定义在 [`trader/constant.py`](../raw/vnpy/vnpy/trader/constant.py)。

事件常量见 [`trader/event.py`](../raw/vnpy/vnpy/trader/event.py)。例如 `EVENT_TICK` 是 `eTick.`，`BaseGateway.on_tick()` 会发出通用行情事件和 `eTick.<vt_symbol>` 合约专属事件。

### 3. BaseGateway：对底层接口的契约

[`trader/gateway.py`](../raw/vnpy/vnpy/trader/gateway.py) 规定连接、关闭、订阅、报单、撤单、资金查询和持仓查询等通用方法。柜台适配层实现这些方法，并通过 `on_tick`、`on_order`、`on_trade` 等回调把标准对象送入 EventEngine。

核心层通过 Gateway 隔离不同柜台的字段和协议差异。适配层还应避免阻塞公共调用，并且不要在事件发出后继续改写已发出的数据对象。

### 4. MainEngine 与 OmsEngine：统一路由和状态缓存

[`trader/engine.py`](../raw/vnpy/vnpy/trader/engine.py) 中，`MainEngine` 启动 EventEngine、注册内置引擎、管理 Gateway 和 App，并提供 `connect`、`subscribe`、`send_order` 等路由方法。调用者不需要直接操作每个柜台的原生 API。

内置 `OmsEngine` 订阅行情、委托、成交、持仓、账户、合约和报价事件，分别按统一 ID 缓存最近状态。`MainEngine` 把常用 OMS 查询函数转接为自己的方法。OMS 也为各 Gateway 建立 `OffsetConverter`，在 CTA 下单前处理交易所开平仓约束。

## 从行情到策略的大致路径

```text
柜台原生回调
  -> Gateway 转换为 TickData / OrderData / TradeData
  -> BaseGateway.on_xxx() 放入 EventEngine 队列
  -> OmsEngine 更新缓存
  -> App 引擎收到相同事件并执行业务逻辑
```

handler 按注册顺序串行处理；应用如果需要耗时任务，应避免直接阻塞事件消费线程。

## 源码细节与边界

- `MainEngine.__init__()` 会将进程当前工作目录切换到 `TRADER_DIR`。以脚本方式集成时，这会影响相对路径文件读写。
- `MainEngine.close()` 先停止事件引擎，再关闭功能引擎和 Gateway。生命周期管理不能只依赖 Python 进程退出。
- OMS 提供的是运行中的状态缓存，不等同于持久化账本；历史留存需要数据库或其他存储组件。
- `BaseGateway` 文档描述了自动重连等实现要求，具体 Gateway 是否、如何实现，必须继续检查对应适配代码。

## 建议阅读顺序

1. [`trader/object.py`](../raw/vnpy/vnpy/trader/object.py) 和 [`trader/constant.py`](../raw/vnpy/vnpy/trader/constant.py)
2. [`event/engine.py`](../raw/vnpy/vnpy/event/engine.py)
3. [`trader/gateway.py`](../raw/vnpy/vnpy/trader/gateway.py)
4. [`trader/engine.py`](../raw/vnpy/vnpy/trader/engine.py) 中的 `MainEngine` 与 `OmsEngine`
5. [CTP Gateway 导读](vnpy-ctp-source-guide.md)

## 来源与版本

- 源码：[`raw/vnpy/`](../raw/vnpy/)，版本 4.4.0，提交 `2ae433660477f9f6a10b0683603879e59072659f`。
- 对照：[原始仓库清单](../raw/README.md)。
