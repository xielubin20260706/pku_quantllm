# SimNow 与 CTP 最小连接 Demo

## 学习目标

根据课程讲义，最小 Demo 的目标是通过 `vnpy_ctp` 从底层 Python 交易 API 开始，完成连接登录、订阅合约行情，并打印行情推送。建议先只完成行情链路，再学习交易下单和策略逻辑。

## SimNow 账号与服务器信息

- SimNow 注册入口：[https://www.simnow.com.cn/](https://www.simnow.com.cn/)
- 服务器地址等连接信息：[SimNow 产品页面](https://www.simnow.com.cn/product.action)

账号、服务器地址和可用时段等信息可能变化；配置连接前请以 SimNow 页面当前公布的内容为准。不要将账号密码提交到公开仓库或交给不可信工具。

## 学习步骤

1. 安装并启动适合当前平台的 VeighNa 环境。
2. 准备 SimNow 账号，并按产品页面配置对应环境、服务器和交易日参数。
3. 使用 `vnpy_ctp` 建立连接并处理登录回报。
4. 登录成功后订阅一个合约，确认行情回调持续到达。
5. 在确认行情链路后，再增加报单、撤单和策略逻辑。

服务器地址和可用合约可能变化，连接前应按 SimNow 当前信息确认地址、接口参数和合约代码。

## 本 Wiki 中的最小 Demo

[`demos/ctp_md_demo.py`](../demos/ctp_md_demo.py) 直接继承 `vnpy_ctp.api.MdApi` 和 `TdApi`，完成 TD 认证/登录及结算单确认、MD 登录、多个合约订阅和 Tick 持续打印，不经过 MainEngine，也不发送委托。程序会持续等待，按 Ctrl+C 退出。当前示例的合约和换月说明见 [`demos/README.md`](../demos/README.md)。

## 故障排查方向

- 检查账号、密码、Broker ID、交易日和服务器地址是否与当前 SimNow 信息一致。
- 区分网络连接失败、认证失败、登录失败和合约订阅失败。
- 查看 VeighNa / CTP 回调及日志，确认请求是否发送以及服务端返回错误码。
- 排查时隐藏账号、密码和其他敏感信息。

## 来源与待验证

- 来源：根目录 `-VeighNa.pdf` 及用户提供的 SimNow 入口。
- 依赖安装和 API 调用方式：待根据实际 VeighNa 版本验证。
