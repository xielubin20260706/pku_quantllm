"""One-shot CTP threshold-order demo built directly on vnpy_ctp APIs.

By default it only prints the order that would be sent. Pass
``--send-simnow-order`` to submit one 1-lot IOC open order to a configuration
marked for SimNow. The environment marker is a guardrail, not proof of the
remote endpoint's identity; review the configured addresses before sending.
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
from pathlib import Path
from threading import Event
from typing import Any

from vnpy_ctp.api import (
    THOST_FTDC_CC_Immediately,
    THOST_FTDC_D_Buy,
    THOST_FTDC_D_Sell,
    THOST_FTDC_FCC_NotForceClose,
    THOST_FTDC_HF_Speculation,
    THOST_FTDC_OF_Open,
    THOST_FTDC_OPT_LimitPrice,
    THOST_FTDC_TC_IOC,
    THOST_FTDC_VC_AV,
)

from ctp_md_demo import (
    DEMO_DIR,
    FLOW_DIR,
    DemoMdApi,
    DemoTdApi,
    load_setting,
    log,
    wait_for,
)


class ThresholdTdApi(DemoTdApi):
    """TD client with instrument lookup and a one-lot limit-order method."""

    def __init__(self, setting: dict[str, Any]) -> None:
        super().__init__(setting)
        self.instrument_done: Event = Event()
        self.instrument: dict[str, Any] = {}
        self.order_ref: int = 0

    def onRspUserLogin(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        max_order_ref = str(data.get("MaxOrderRef", "0") or "0")
        if max_order_ref.isdigit():
            self.order_ref = int(max_order_ref)
        super().onRspUserLogin(data, error, reqid, last)

    def onRspQryInstrument(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        if int(error.get("ErrorID", 0) or 0):
            log(f"[TD] 合约查询失败：{error.get('ErrorMsg', '')}")
        elif data.get("InstrumentID"):
            self.instrument = data
        if last:
            self.instrument_done.set()

    def query_instrument(self, symbol: str) -> dict[str, Any]:
        self.reqid += 1
        request: dict[str, str] = {"InstrumentID": symbol}
        code: int = self.reqQryInstrument(request, self.reqid)
        if code:
            raise RuntimeError(f"[TD] 合约查询请求发送失败，返回码={code}")
        wait_for(self.instrument_done, "TD 合约查询响应", 20)
        if self.instrument.get("InstrumentID") != symbol:
            raise RuntimeError(f"[TD] 柜台未返回合约 {symbol} 的信息")
        if not self.instrument.get("ExchangeID"):
            raise RuntimeError(f"[TD] 合约 {symbol} 缺少 ExchangeID")
        return self.instrument

    def send_one_lot_ioc_open(
        self, symbol: str, exchange: str, direction: str, price: float
    ) -> int:
        """Send one IOC limit order; return the local API request status."""
        self.order_ref += 1
        self.reqid += 1
        request: dict[str, Any] = {
            "InstrumentID": symbol,
            "ExchangeID": exchange,
            "LimitPrice": price,
            "VolumeTotalOriginal": 1,
            "OrderPriceType": THOST_FTDC_OPT_LimitPrice,
            "Direction": THOST_FTDC_D_Buy if direction == "buy" else THOST_FTDC_D_Sell,
            "CombOffsetFlag": THOST_FTDC_OF_Open,
            "CombHedgeFlag": THOST_FTDC_HF_Speculation,
            "OrderRef": str(self.order_ref),
            "InvestorID": self.setting["userid"],
            "UserID": self.setting["userid"],
            "BrokerID": self.setting["brokerid"],
            "ContingentCondition": THOST_FTDC_CC_Immediately,
            "ForceCloseReason": THOST_FTDC_FCC_NotForceClose,
            "IsAutoSuspend": 0,
            "TimeCondition": THOST_FTDC_TC_IOC,
            "VolumeCondition": THOST_FTDC_VC_AV,
            "MinVolume": 1,
        }
        return int(self.reqOrderInsert(request, self.reqid))

    def onRspOrderInsert(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        error_id = int(error.get("ErrorID", 0) or 0)
        if error_id:
            log(f"[TD] 报单响应拒绝：ErrorID={error_id}, ErrorMsg={error.get('ErrorMsg', '')}")
        else:
            log(f"[TD] 报单请求已受理，OrderRef={data.get('OrderRef', '')}")

    def onErrRtnOrderInsert(self, data: dict, error: dict) -> None:
        log(
            f"[TD] 报单错误回报：InstrumentID={data.get('InstrumentID', '')}, "
            f"ErrorID={error.get('ErrorID', 0)}, ErrorMsg={error.get('ErrorMsg', '')}"
        )

    def onRtnOrder(self, data: dict) -> None:
        log(
            f"[ORDER] {data.get('InstrumentID', '')} "
            f"ref={data.get('OrderRef', '')} status={data.get('OrderStatus', '')} "
            f"price={data.get('LimitPrice', '')} "
            f"volume={data.get('VolumeTraded', 0)}/{data.get('VolumeTotalOriginal', 0)}"
        )

    def onRtnTrade(self, data: dict) -> None:
        log(
            f"[TRADE] {data.get('InstrumentID', '')} "
            f"price={data.get('Price', '')} volume={data.get('Volume', 0)} "
            f"direction={data.get('Direction', '')} offset={data.get('OffsetFlag', '')}"
        )


class ThresholdMdApi(DemoMdApi):
    """Market-data API that forwards target ticks to the main loop."""

    def __init__(self, setting: dict[str, Any], ticks: queue.Queue[tuple[str, float]]) -> None:
        super().__init__(setting)
        self.ticks = ticks

    def onRtnDepthMarketData(self, data: dict) -> None:
        super().onRtnDepthMarketData(data)
        try:
            symbol = str(data.get("InstrumentID", ""))
            price = float(data.get("LastPrice", 0) or 0)
            if symbol and math.isfinite(price) and price > 0:
                self.ticks.put_nowait((symbol, price))
        except (TypeError, ValueError, queue.Full):
            log("[MD] 忽略无效行情价格")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CTP 单次阈值触发限价单教学 Demo")
    parser.add_argument("--setting", type=Path, default=DEMO_DIR / "ctp_demo_setting.json")
    parser.add_argument("--symbol", required=True, help="触发并交易的合约，例如 rb2701")
    parser.add_argument("--above", type=float, required=True, help="LastPrice 严格高于此价时触发")
    parser.add_argument("--direction", choices=("buy", "sell"), default="buy")
    parser.add_argument(
        "--send-simnow-order",
        action="store_true",
        help="确认后向配置中的 SimNow 账户发送一笔 1 手 IOC 开仓限价单；默认只演练",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.above) or args.above <= 0:
        print("--above 必须是有限正数", file=sys.stderr)
        return 2
    try:
        setting = load_setting(args.setting)
    except (OSError, ValueError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.symbol not in setting["symbols"]:
        print(f"合约 {args.symbol} 不在配置文件 symbols 中", file=sys.stderr)
        return 2
    if args.send_simnow_order and (
        str(setting.get("environment", "")).lower() != "simnow"
        or setting.get("appid") != "simnow_client_test"
    ):
        print(
            "拒绝发送委托：实际模拟报单要求配置 environment=simnow 且 "
            "appid=simnow_client_test。",
            file=sys.stderr,
        )
        return 2

    td_api = ThresholdTdApi(setting)
    ticks: queue.Queue[tuple[str, float]] = queue.Queue(maxsize=1000)
    md_api = ThresholdMdApi(setting, ticks)
    order_attempted = False
    try:
        FLOW_DIR.mkdir(parents=True, exist_ok=True)
        if args.send_simnow_order:
            log("[MODE] 已启用 SimNow 实际模拟报单：最多一笔、1 手、IOC、开仓")
        else:
            log("[MODE] 只演练，不发送委托；如需向 SimNow 发一笔单，显式传 --send-simnow-order")

        td_api.connect_and_login(FLOW_DIR / "td")
        instrument = td_api.query_instrument(args.symbol)
        exchange = str(instrument["ExchangeID"])
        tick_size = float(instrument.get("PriceTick", 0) or 0)
        log(f"[TD] 合约就绪：{args.symbol}.{exchange} 最小价位={tick_size:g}")

        md_api.connect(FLOW_DIR / "md")
        md_api.subscribe([args.symbol])
        log(
            f"监控 {args.symbol}：LastPrice > {args.above:g} 时，"
            f"{args.direction.upper()} 1 手 IOC 限价单；Ctrl+C 退出。"
        )

        stop = Event()
        while not stop.is_set():
            try:
                symbol, price = ticks.get(timeout=1)
            except queue.Empty:
                continue
            if order_attempted or symbol != args.symbol or price <= args.above:
                continue

            order_attempted = True
            if not args.send_simnow_order:
                log(
                    f"[DRY-RUN] 条件触发：{symbol} last={price:g} > {args.above:g}；"
                    f"计划 {args.direction.upper()} OPEN 1 手，IOC 限价={price:g}"
                )
                continue

            code = td_api.send_one_lot_ioc_open(
                args.symbol, exchange, args.direction, price
            )
            if code:
                log(f"[TD] 报单请求发送失败，返回码={code}")
            else:
                log(
                    f"[TD] 已提交 {args.direction.upper()} OPEN 1 手 IOC 限价单，"
                    f"{args.symbol}.{exchange} price={price:g}；等待柜台委托/成交回报"
                )
    except (TimeoutError, RuntimeError, OSError) as exc:
        log(f"Demo 运行失败：{exc}")
        return 1
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在关闭 CTP API…")
    finally:
        try:
            md_api.close()
        finally:
            td_api.close()
        log(f"已关闭 API；本次收到行情条数：{md_api.tick_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
