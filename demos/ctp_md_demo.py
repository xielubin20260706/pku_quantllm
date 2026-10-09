"""Direct vnpy_ctp API demo: login to SimNow, subscribe and print market ticks.

This demo intentionally uses vnpy_ctp.api.MdApi and TdApi directly instead of
VeighNa's MainEngine/Gateway layer. It does not submit any orders.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from threading import Event
from typing import Any

from vnpy_ctp.api import MdApi, TdApi


DEMO_DIR: Path = Path(__file__).resolve().parent
FLOW_DIR: Path = DEMO_DIR / ".ctp_flow"
DEFAULT_SETTING: Path = DEMO_DIR / "ctp_demo_setting.json"
DEFAULT_SYMBOLS: list[str] = ["rb2701", "hc2701", "i2701", "m2701"]


def log(message: str) -> None:
    """Print callback progress immediately, including from native callback threads."""
    print(message, flush=True)


def response_error(error: dict[str, Any]) -> str:
    """Format a CTP response error without dumping credentials or full request data."""
    error_id: int = int(error.get("ErrorID", 0) or 0)
    error_msg: str = str(error.get("ErrorMsg", "") or "").strip()
    return f"ErrorID={error_id}, ErrorMsg={error_msg}"


class DemoMdApi(MdApi):
    """Minimal direct market-data API client."""

    def __init__(self, setting: dict[str, str]) -> None:
        super().__init__()
        self.setting: dict[str, str] = setting
        self.reqid: int = 0
        self.front_connected: Event = Event()
        self.login_done: Event = Event()
        self.login_ok: bool = False
        self.subscription_done: Event = Event()
        self.requested_symbols: set[str] = set()
        self.subscribed_symbols: set[str] = set()
        self.subscription_errors: dict[str, str] = {}
        self.tick_count: int = 0
        self.started: bool = False

    def onFrontConnected(self) -> None:
        log("[MD] 行情前置连接成功")
        self.front_connected.set()

    def onFrontDisconnected(self, reason: int) -> None:
        log(f"[MD] 行情前置连接断开，原因码={reason}；本 Demo 未实现重连后的重新登录/订阅")

    def onRspUserLogin(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        self.login_ok = int(error.get("ErrorID", 0) or 0) == 0
        if self.login_ok:
            log(f"[MD] 行情登录成功，交易日={data.get('TradingDay', '')}")
        else:
            log(f"[MD] 行情登录失败：{response_error(error)}")
        if last:
            self.login_done.set()

    def onRspError(self, error: dict, reqid: int, last: bool) -> None:
        log(f"[MD] 请求错误：{response_error(error)}")

    def onRspSubMarketData(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        error_id: int = int(error.get("ErrorID", 0) or 0)
        instrument: str = data.get("InstrumentID", "")
        if error_id:
            message: str = response_error(error)
            log(f"[MD] 合约 {instrument} 订阅失败：{message}")
            self.subscription_errors[instrument] = message
        else:
            log(f"[MD] 合约 {instrument} 订阅请求已确认")
            self.subscribed_symbols.add(instrument)
        if last:
            self.subscription_done.set()

    def onRtnDepthMarketData(self, data: dict) -> None:
        self.tick_count += 1
        log(
            "[TICK] "
            f"{data.get('InstrumentID', '')} "
            f"{data.get('ActionDay', '')} {data.get('UpdateTime', '')}."
            f"{int(data.get('UpdateMillisec', 0) or 0):03d} "
            f"last={data.get('LastPrice', 0)} "
            f"bid1={data.get('BidPrice1', 0)}x{data.get('BidVolume1', 0)} "
            f"ask1={data.get('AskPrice1', 0)}x{data.get('AskVolume1', 0)} "
            f"volume={data.get('Volume', 0)} "
            f"open_interest={data.get('OpenInterest', 0)}"
        )

    def connect(self, flow_path: Path) -> None:
        flow_path.mkdir(parents=True, exist_ok=True)
        # SimNow is a test environment: production_mode must be False.
        self.createFtdcMdApi(str(flow_path).encode("GBK"), False)
        self.registerFront(self.setting["md_address"])
        self.init()
        self.started = True

        wait_for(self.front_connected, "MD 行情前置连接", 20)
        self.reqid += 1
        request: dict[str, str] = {
            "UserID": self.setting["userid"],
            "Password": self.setting["password"],
            "BrokerID": self.setting["brokerid"],
        }
        code: int = self.reqUserLogin(request, self.reqid)
        if code:
            raise RuntimeError(f"[MD] 登录请求未能发送，返回码={code}")
        wait_for(self.login_done, "MD 行情登录响应", 20)
        if not self.login_ok:
            raise RuntimeError("[MD] 行情登录失败，请检查账号和 SimNow 行情前置配置")

    def subscribe(self, symbols: list[str]) -> None:
        self.requested_symbols = set(symbols)
        code: int = self.subscribeMarketData(symbols)
        if code:
            raise RuntimeError(f"[MD] 订阅请求未能发送，返回码={code}")
        wait_for(self.subscription_done, "合约行情订阅响应", 15)

        unconfirmed: set[str] = self.requested_symbols - self.subscribed_symbols - set(
            self.subscription_errors
        )
        if unconfirmed:
            log(f"[MD] 以下合约未收到订阅确认：{', '.join(sorted(unconfirmed))}")
        if not self.subscribed_symbols:
            raise RuntimeError("[MD] 没有合约订阅成功，请检查合约代码和 SimNow 行情权限")
        if self.subscription_errors:
            log(f"[MD] 订阅失败合约：{', '.join(sorted(self.subscription_errors))}")

    def close(self) -> None:
        if self.started:
            self.exit()
            self.started = False


class DemoTdApi(TdApi):
    """Minimal direct trading API client used only to demonstrate TD login."""

    def __init__(self, setting: dict[str, str]) -> None:
        super().__init__()
        self.setting: dict[str, str] = setting
        self.reqid: int = 0
        self.front_connected: Event = Event()
        self.auth_done: Event = Event()
        self.auth_ok: bool = False
        self.login_done: Event = Event()
        self.login_ok: bool = False
        self.settlement_done: Event = Event()
        self.settlement_ok: bool = False
        self.started: bool = False

    def onFrontConnected(self) -> None:
        log("[TD] 交易前置连接成功")
        self.front_connected.set()

    def onFrontDisconnected(self, reason: int) -> None:
        log(f"[TD] 交易前置连接断开，原因码={reason}；本 Demo 未实现重连后的重新认证/登录")

    def onRspAuthenticate(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        self.auth_ok = int(error.get("ErrorID", 0) or 0) == 0
        if self.auth_ok:
            log("[TD] 客户端认证成功")
        else:
            log(f"[TD] 客户端认证失败：{response_error(error)}")
        if last:
            self.auth_done.set()

    def onRspUserLogin(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        self.login_ok = int(error.get("ErrorID", 0) or 0) == 0
        if self.login_ok:
            log(f"[TD] 交易登录成功，交易日={data.get('TradingDay', '')}")
        else:
            log(f"[TD] 交易登录失败：{response_error(error)}")
        if last:
            self.login_done.set()

    def onRspSettlementInfoConfirm(
        self, data: dict, error: dict, reqid: int, last: bool
    ) -> None:
        self.settlement_ok = int(error.get("ErrorID", 0) or 0) == 0
        if self.settlement_ok:
            log("[TD] 结算单确认成功（本 Demo 不发送委托）")
        else:
            log(f"[TD] 结算单确认失败：{response_error(error)}")
        if last:
            self.settlement_done.set()

    def onRspError(self, error: dict, reqid: int, last: bool) -> None:
        log(f"[TD] 请求错误：{response_error(error)}")

    def connect_and_login(self, flow_path: Path) -> None:
        flow_path.mkdir(parents=True, exist_ok=True)
        # SimNow is a test environment: production_mode must be False.
        self.createFtdcTraderApi(str(flow_path).encode("GBK"), False)
        # Restart from the latest public/private topic data for this demo.
        self.subscribePrivateTopic(0)
        self.subscribePublicTopic(0)
        self.registerFront(self.setting["td_address"])
        self.init()
        self.started = True

        wait_for(self.front_connected, "TD 交易前置连接", 20)

        self.reqid += 1
        auth_request: dict[str, str] = {
            "UserID": self.setting["userid"],
            "BrokerID": self.setting["brokerid"],
            "AuthCode": self.setting["auth_code"],
            "AppID": self.setting["appid"],
            "UserProductInfo": self.setting.get("product_info", ""),
        }
        code: int = self.reqAuthenticate(auth_request, self.reqid)
        if code:
            raise RuntimeError(f"[TD] 认证请求未能发送，返回码={code}")
        wait_for(self.auth_done, "TD 客户端认证响应", 20)
        if not self.auth_ok:
            raise RuntimeError("[TD] 交易客户端认证失败，请检查 AppID 和 AuthCode")

        self.reqid += 1
        login_request: dict[str, str] = {
            "UserID": self.setting["userid"],
            "Password": self.setting["password"],
            "BrokerID": self.setting["brokerid"],
        }
        code = self.reqUserLogin(login_request, self.reqid)
        if code:
            raise RuntimeError(f"[TD] 登录请求未能发送，返回码={code}")
        wait_for(self.login_done, "TD 交易登录响应", 20)
        if not self.login_ok:
            raise RuntimeError("[TD] 交易登录失败，请检查账号和 SimNow 交易前置配置")

        # Match the normal VeighNa CTP gateway startup flow.
        self.reqid += 1
        settlement_request: dict[str, str] = {
            "BrokerID": self.setting["brokerid"],
            "InvestorID": self.setting["userid"],
        }
        code = self.reqSettlementInfoConfirm(settlement_request, self.reqid)
        if code:
            raise RuntimeError(f"[TD] 结算单确认请求未能发送，返回码={code}")
        wait_for(self.settlement_done, "TD 结算单确认响应", 20)
        if not self.settlement_ok:
            raise RuntimeError("[TD] 结算单确认失败")

    def close(self) -> None:
        if self.started:
            self.exit()
            self.started = False


def wait_for(event: Event, description: str, timeout: float) -> None:
    """Wait for a native API callback and report a useful timeout message."""
    if not event.wait(timeout):
        raise TimeoutError(f"等待{description}超时（{timeout:g} 秒）")


def load_setting(path: Path) -> dict[str, str]:
    """Load local SimNow config and validate required fields."""
    if not path.exists():
        raise FileNotFoundError(
            f"找不到配置文件：{path}\n"
            f"请复制 {DEMO_DIR / 'ctp_demo_setting.example.json'} 为 {DEFAULT_SETTING}，"
            "再填入本机使用的 SimNow 账号信息。"
        )

    with path.open("r", encoding="utf-8") as file:
        loaded: Any = json.load(file)
    if not isinstance(loaded, dict):
        raise ValueError("配置文件最外层必须是 JSON 对象")
    setting: dict[str, str] = loaded

    required: tuple[str, ...] = (
        "userid",
        "password",
        "brokerid",
        "td_address",
        "md_address",
        "appid",
        "auth_code",
    )
    missing: list[str] = [
        key for key in required if not str(setting.get(key, "")).strip()
    ]
    if missing:
        raise ValueError(f"配置文件缺少必填项：{', '.join(missing)}")

    wrong_types: list[str] = [
        key for key in required if not isinstance(setting.get(key), str)
    ]
    if wrong_types:
        raise ValueError(f"以下配置项必须是字符串：{', '.join(wrong_types)}")

    placeholders: tuple[str, ...] = ("在此填写", "从SimNow", "填写一个")
    for key in required:
        if any(marker in setting[key] for marker in placeholders):
            raise ValueError(f"请先在配置文件中填写真实的本地配置项：{key}")

    for key in ("td_address", "md_address"):
        if not setting[key].startswith(("tcp://", "ssl://", "socks")):
            raise ValueError(f"{key} 需要包含 CTP 地址协议头，例如 tcp://")

    symbols: Any = setting.get("symbols")
    if symbols is None:
        # Keep the demo multi-contract by default, including for older local configs.
        legacy_symbol: Any = setting.get("symbol")
        symbols = [legacy_symbol] if isinstance(legacy_symbol, str) and legacy_symbol else []
        symbols.extend(DEFAULT_SYMBOLS)
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("symbols 必须是至少包含一个合约代码的 JSON 字符串数组")
    if any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols):
        raise ValueError("symbols 中的每个合约代码都必须是非空字符串")
    setting["symbols"] = list(dict.fromkeys(symbol.strip() for symbol in symbols))

    return setting


def parse_args() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="通过 vnpy_ctp 底层 Python API 登录 SimNow 并打印行情"
    )
    parser.add_argument(
        "--setting",
        type=Path,
        default=DEFAULT_SETTING,
        help=f"本地 JSON 配置路径（默认：{DEFAULT_SETTING}）",
    )
    return parser.parse_args()


def main() -> int:
    args: argparse.Namespace = parse_args()
    try:
        setting: dict[str, str] = load_setting(args.setting)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    md_api: DemoMdApi = DemoMdApi(setting)
    td_api: DemoTdApi = DemoTdApi(setting)
    try:
        FLOW_DIR.mkdir(parents=True, exist_ok=True)
        log("启动底层 CTP API；本 Demo 只登录、订阅和打印行情，不发送委托。")
        td_api.connect_and_login(FLOW_DIR / "td")

        md_api.connect(FLOW_DIR / "md")
        md_api.subscribe(setting["symbols"])

        log(
            f"持续等待行情：{', '.join(setting['symbols'])}。"
            "收到行情时会逐条打印；按 Ctrl+C 才会退出。"
        )
        stop: Event = Event()

        def request_stop(signum: int, frame: Any) -> None:
            log("收到停止信号，正在关闭 CTP API…")
            stop.set()

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)

        while not stop.wait(1):
            pass
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
