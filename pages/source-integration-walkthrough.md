# 三模块端到端调用链

本页把三个仓库连成一次从启动到成交的路径。用于建立源码地图；具体柜台字段和运行配置仍应查看对应版本文档。

## 行情到策略

1. 应用创建 `EventEngine`、`MainEngine`，注册 `CtpGateway` 和 `CtaStrategyApp`。
2. `MainEngine.connect()` 将连接配置路由到 `CtpGateway.connect()`。
3. `CtpMdApi` 连接并登录行情前置；`CtpTdApi` 独立执行授权/登录、结算确认和合约查询。
4. CTP 深度行情回调在 `CtpMdApi.onRtnDepthMarketData()` 中被转换为标准 `TickData`。
5. `BaseGateway.on_tick()` 将事件放进 `EventEngine`。`OmsEngine` 缓存 Tick，CTA 引擎也接收 Tick 并按 `vt_symbol` 调用相应策略的 `on_tick()`。

## 策略到订单/成交

1. 策略回调调用 `buy` / `sell` / `short` / `cover`。
2. `CtaEngine.send_order()` 找到合约、按最小价位和数量取整，处理服务器停止单或本地停止单。
3. 普通委托经 `MainEngine.convert_order_request()` 做 offset 转换，再由 `MainEngine.send_order()` 路由至 CTP Gateway。
4. `CtpTdApi.send_order()` 将标准请求映射为 CTP 字段并提交原生 API；委托/成交 CTP 回调再被转换为标准 `OrderData` / `TradeData` 事件。
5. OMS 更新状态缓存；CTA 根据订单关联表找到策略，回调 `on_order()` / `on_trade()`，并更新策略持仓和状态。

## 源码跳转

- [vn.py 核心框架](vnpy-core-source-guide.md)
- [CTP Gateway](vnpy-ctp-source-guide.md)
- [CTA Strategy](vnpy-ctastrategy-source-guide.md)
- [SimNow 与 CTP 学习准备](simnow-ctp-demo.md)

## 练习建议

先用测试环境观察登录日志、合约事件和 Tick 回调；随后跟踪一笔模拟委托从策略请求到 CTP 回报，再确认 OMS 缓存和策略回调的状态变化。每一步记录事件类型、关键 ID、线程/回调来源和状态字段。
