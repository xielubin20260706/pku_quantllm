# vnpy_ctp 源码导读

## 阅读范围

重点阅读 [`vnpy_ctp/gateway/ctp_gateway.py`](../raw/vnpy_ctp/vnpy_ctp/gateway/ctp_gateway.py)、包入口和 `pyproject.toml`。它们展示 Python 适配层如何与 CTP API 绑定交互；原生 C++ API 和编译过程不在本页展开。

## 一句话理解

`CtpGateway` 将核心框架的统一请求翻译成 CTP MD/TD 请求，再把 CTP 回调中的字典、代码和时间字段转换成 vn.py 标准对象并发布事件。

## 分层结构

- `CtpGateway` 实现核心 `BaseGateway` 接口，同时持有行情端 `CtpMdApi` 和交易端 `CtpTdApi`。
- `vnpy_ctp.api` 导出底层 `MdApi`、`TdApi` 和 CTP 常量；平台相关的原生库和构建配置位于 `api/`、`meson.build` 等目录。
- 映射表将方向、开平、委托状态、交易所、产品和订单类型在 CTP 字段与框架枚举间转换。

## 连接与行情链路

1. `CtpGateway.connect(setting)` 读取用户名、密码、经纪商、交易/行情地址和认证信息，并分别创建 MD 与 TD 连接。
2. 两个 API 在 `onFrontConnected()` 回调后发起各自登录流程；行情侧登录成功后补订之前缓存的订阅请求。
3. TD 侧在配置了授权码时先认证，再登录；登录成功后自动确认结算单并开始查询合约。
4. `onRspQryInstrument()` 将合约元数据转换成 `ContractData`。在合约表初始化前到达的委托和成交推送会暂存，等合约信息齐备后再处理。
5. `onRtnDepthMarketData()` 过滤无时间戳或缺少合约信息的行情，修正交易日/时区和异常浮点数，构造 `TickData` 后调用 `gateway.on_tick()`。

行情与交易连接是两条独立链路。连接调用返回不意味着登录和订阅已经完成；应观察登录、合约查询和订阅回报日志，再确认行情事件到达。

## 交易链路

- `send_order()` 先检查该版本支持的开平方式和委托类型，将 `OrderRequest` 映射为 CTP 报单字段。
- 发送成功后立即生成并推送一个提交中的标准委托对象；后续 CTP 委托/成交回调继续更新标准状态。
- CTP 委托标识由 `FrontID_SessionID_OrderRef` 组成；交易所系统单号通过映射关联到框架委托号。
- `onRtnOrder()` 将状态、方向、开平、价格、成交量和时间转换为 `OrderData`；拒单、撤单等状态需结合 CTP 原始字段判断。
- `onRtnTrade()` 将成交推送转成 `TradeData`。成交回调依赖前面建立的系统单号映射，因此委托和成交的到达顺序、初始化缓冲值得跟踪。
- 账号和持仓查询由定时器轮流触发，降低查询频率和流控压力。

## 配置提示

Gateway 的默认配置字段为中文：用户名、密码、经纪商代码、交易服务器、行情服务器、产品名称、授权编码、柜台环境和产品信息。源码将未带协议头的地址补成 `tcp://`。`柜台环境` 未提供时默认取“实盘”，因此连接 SimNow 时应明确按当前课程环境选择测试环境并使用 SimNow 给出的对应地址和参数。

不要把账号密码、授权码或完整连接配置提交到 Wiki 或公开仓库。CTP 环境、交易日、服务器和账号权限等信息以当前 SimNow 公告和实际账户配置为准。

## 源码阅读时发现的边界

- Gateway 的 Python `onFrontDisconnected()` 回调只更新登录状态并写日志；本文件这一层没有直接发起显式重连。若要确认整体重连行为，还需检查底层 API 库的行为和部署环境。
- 合约缓存 `symbol_contract_map` 是模块级字典，以 `InstrumentID` 作为键；阅读多市场或多 Gateway 场景时，应确认合约代码冲突的处理约束。
- CTP 支持哪些委托类型和状态映射由当前文件的映射表决定；不要把其他柜台的能力假设套到 CTP 上。
- `vnpy_ctp` 的 `pyproject.toml` 只声明 `vnpy>=3.0.0`，不代表它与所有满足下限的核心版本组合都做过测试。

## 建议阅读顺序

1. [`gateway/ctp_gateway.py`](../raw/vnpy_ctp/vnpy_ctp/gateway/ctp_gateway.py) 中的映射表和 `CtpGateway`
2. `CtpMdApi`：`connect`、`login`、`subscribe`、`onRtnDepthMarketData`
3. `CtpTdApi`：`connect`、认证/登录、合约查询、`send_order`、`onRtnOrder`、`onRtnTrade`
4. [`vnpy_ctp/pyproject.toml`](../raw/vnpy_ctp/pyproject.toml) 和 README 的原生依赖 / 编译说明
5. [vn.py 核心框架](vnpy-core-source-guide.md) 中的 `BaseGateway` 和 OMS

## 来源与版本

- 源码：[`raw/vnpy_ctp/`](../raw/vnpy_ctp/)，包版本 6.7.11.4，提交 `1274a048027ba204d0dde03953384ee7e2513979`。
- 对照：[原始仓库清单](../raw/README.md)及 [SimNow/CTP 学习页](simnow-ctp-demo.md)。
