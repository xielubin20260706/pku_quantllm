# vnpy_ctp 底层 API 行情 Demo

这个 Demo 直接继承 `vnpy_ctp.api.MdApi` 和 `TdApi`，不启动 `MainEngine`、VeighNa UI 或 CTA 引擎。程序会：

1. 连接 SimNow 交易前置，认证并登录，确认结算单；
2. 连接行情前置并登录；
3. 订阅配置里的多个合约；
4. 在 `onRtnDepthMarketData` 中持续打印多个合约的 Tick；
5. 程序保持运行，按 Ctrl+C 才关闭两个 API。

程序只登录和订阅行情，不发送委托。

## 准备配置

先在当前 Python 环境安装与课程匹配的 VeighNa 和 `vnpy_ctp`。然后复制 `ctp_demo_setting.example.json` 为 `ctp_demo_setting.json`，填写 SimNow 账号、密码、服务器和当前可订阅的合约代码数组。示例预选 `rb2701`、`hc2701`、`i2701`、`m2701`，覆盖螺纹钢、热卷、铁矿石和豆粕。选择参考了 2026-09-30 的公开行情：螺纹 `rb2701` 当日成交量约 63.9 万手，铁矿 `i2701` 约 21.86 万手；[螺纹行情](https://hq.smm.cn/futures/104143048)、[铁矿行情](https://www.mysteel.com/hot/1123494.html)。这是用于观察行情的示例列表，不保证一直是成交量排名靠前的合约。主力合约会换月，运行前请按 SimNow 当前合约列表调整 `symbols`。

真实配置文件已加入本目录 `.gitignore`；建议将文件权限限制为当前用户可读写（macOS/Linux 可运行 `chmod 600 demos/ctp_demo_setting.json`）。账号密码没有写入源码或示例配置。

SimNow 服务器地址可能变化，请从[产品页面](https://www.simnow.com.cn/product.action)确认当前交易日适用的 TD/MD 地址。配置的 `symbols` 也应选当前有效的合约。

## 运行

在项目根目录使用已安装 `vnpy_ctp` 的 Python 环境运行：

```bash
python demos/ctp_md_demo.py
```

也可以传入其他 JSON 配置文件：

```bash
python demos/ctp_md_demo.py --setting /path/to/ctp_demo_setting.json
```

如果等待连接、登录、认证或结算确认超时，先检查网络、账号权限、服务器地址及系统时间。订阅得到成功响应但暂时没有 Tick，通常还需确认合约代码和 SimNow 当前是否有行情推送。

收到 Tick 后，程序会保持运行并持续打印；没有 Tick 时也会留在等待状态，按 Ctrl+C 才退出。这个教学 Demo 没有实现断线后的应用层重新认证、登录和重新订阅；断线后请重启程序。实际交易程序需要实现并验证完整的重连与状态恢复流程。

## 代码路径

- 行情 API 和打印回调：`ctp_md_demo.py` 中的 `DemoMdApi`
- 交易 API 登录/结算确认：`ctp_md_demo.py` 中的 `DemoTdApi`
- 对应底层 API 包：[`../raw/vnpy_ctp/vnpy_ctp/api/`](../raw/vnpy_ctp/vnpy_ctp/api/)
- Gateway 封装对照：[`../raw/vnpy_ctp/vnpy_ctp/gateway/ctp_gateway.py`](../raw/vnpy_ctp/vnpy_ctp/gateway/ctp_gateway.py)

# CTP 单次阈值下单 Demo

[`ctp_threshold_order_demo.py`](ctp_threshold_order_demo.py) 在底层 TD/MD API 上演示单合约阈值触发：目标合约最新价严格高于 `--above` 后，只触发一次，计划以当时最新价发出 1 手 IOC 限价开仓单，并持续接收委托/成交回报。

默认只打印 DRY-RUN 计划，不发送委托：

```bash
python demos/ctp_threshold_order_demo.py \
  --symbol rb2701 --above 3200 --direction buy
```

`3200` 只是命令示例，运行前应按当前行情、合约最小价位和交易方向重新设定阈值。实际 SimNow 模拟报单需显式添加 `--send-simnow-order`，并在本地配置中设置 `"environment": "simnow"`、使用 `simnow_client_test` AppID。程序限制为最多一笔、1 手 IOC 开仓委托；IOC 仍可能部分成交。只有柜台订单/成交回报能确认接收和成交，`reqOrderInsert` 返回 0 只代表本地 API 接受了请求。

不要将实际报单模式用于实盘配置。此 Demo 不做自动撤单、仓位管理、止损或重连；示例代码的默认行为是 DRY-RUN。账号配置保留在本机的 `ctp_demo_setting.json`，不要提交。
