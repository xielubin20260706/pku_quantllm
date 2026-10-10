#!/usr/bin/env python3
"""Local web UI for the SSE50 replay and isolated simulated account."""

from __future__ import annotations

import bisect
import json
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "exchange_simulation_output"
L5_PATH = OUTPUT / "l5_1s_20260923.parquet"
TRADES_PATH = OUTPUT / "trades_replayed_20260923.parquet"
L2_DATASETS = {
    "ord": ROOT / "ord_20260923.parquet",
    "exe": ROOT / "exe_20260923.parquet",
    "snp": ROOT / "snp_20260923.parquet",
}
WEB_DIR = ROOT / "web"
PRICE_SCALE = 10_000
LOT_SIZE = 100
INITIAL_CASH = 1_000_000.0


def price_ticks(value: float) -> int:
    return int(round(float(value) * PRICE_SCALE))


def raw_time_text(raw: int) -> str:
    hour = raw // 10_000_000
    minute = (raw // 100_000) % 100
    second = (raw // 1_000) % 100
    millis = raw % 1_000
    return f"{hour:02d}:{minute:02d}:{second:02d}.{millis:03d}"


def raw_time_bound(value: str) -> int | None:
    if not value:
        return None
    parts = [int(part) for part in value.split(":")]
    hour, minute = parts[:2]
    second = parts[2] if len(parts) > 2 else 0
    return hour * 10_000_000 + minute * 100_000 + second * 1_000


@lru_cache(maxsize=3)
def l2_symbol_frame(dataset: str, symbol: int) -> pd.DataFrame:
    path = L2_DATASETS[dataset]
    frame = pd.read_parquet(path, filters=[("Symbol", "==", symbol)])
    sort_columns = [column for column in ["Time", "BizIndex", "Index"] if column in frame.columns]
    return frame.sort_values(sort_columns, kind="stable").reset_index(drop=True) if sort_columns else frame


def json_safe_records(frame: pd.DataFrame) -> list[dict]:
    records = []
    for record in frame.to_dict("records"):
        row = {}
        for key, value in record.items():
            if pd.isna(value):
                row[key] = None
            elif isinstance(value, np.generic):
                row[key] = value.item()
            else:
                row[key] = value
        records.append(row)
    return records


def evaluate_strategy_idea(body: dict) -> dict:
    idea = str(body.get("idea", "")).strip()
    feature = str(body.get("feature", ""))
    direction = str(body.get("direction", "high"))
    horizon = int(body.get("horizon", 5))
    quantile = float(body.get("quantile", 0.2))
    cost_bps = float(body.get("cost_bps", 0))
    if not idea:
        raise ValueError("请先描述你的策略想法")
    if len(idea) > 500:
        raise ValueError("想法描述不能超过 500 个字符")
    if direction not in ("high", "low"):
        raise ValueError("方向只支持高特征值或低特征值")
    if horizon not in (5, 10, 15):
        raise ValueError("预测周期只支持 5、10 或 15 分钟")
    if not 0.1 <= quantile <= 0.4:
        raise ValueError("分位范围应在 10% 到 40% 之间")
    if not np.isfinite(cost_bps) or not 0 <= cost_bps <= 1_000:
        raise ValueError("估算总成本需在 0 到 1000 bp 之间")

    report_path = OUTPUT / "l2_research_20260923.json"
    if not report_path.exists():
        raise ValueError("请先运行 python generate_l2_research.py 生成分析报告")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    known = {item["feature"]: item for item in report["strategy_screen"]["features"]}
    if feature not in known:
        raise ValueError("该特征当前不在可计算目录中")
    requested_symbols = body.get("symbols") or []
    available_symbols = set(report["scope"]["symbol_codes"])
    try:
        requested_symbols = sorted({int(value) for value in requested_symbols})
    except (TypeError, ValueError):
        raise ValueError("证券代码格式错误") from None
    if any(symbol not in available_symbols for symbol in requested_symbols):
        raise ValueError("所选证券不在当前 L2 样本中")

    sample_frame = pd.DataFrame(report["strategy_screen"]["samples"])
    target = f"forward_{horizon}m_return"
    sample_frame = sample_frame.dropna(subset=[feature, target]).copy()
    if requested_symbols:
        sample_frame = sample_frame.loc[sample_frame.Symbol.isin(requested_symbols)]
    if sample_frame.empty:
        raise ValueError("所选特征和周期没有足够有效数据")

    measurements = []
    selected_signal_returns = []
    cross_sectional = not requested_symbols or len(requested_symbols) >= 10
    if cross_sectional:
        # Cross-sectional ranking is recalculated within each signal timestamp.
        for _, frame in sample_frame.groupby("bucket_seconds"):
            if len(frame) < 10 or frame[feature].nunique() < 2:
                continue
            ranked = frame.sort_values(feature)
            count = max(1, int(np.ceil(len(ranked) * quantile)))
            low, high = ranked.head(count), ranked.tail(count)
            selected, opposite = (high, low) if direction == "high" else (low, high)
            selected_signal_returns.extend(selected[target].astype(float).tolist())
            measurements.append({
                "selected_return": float(selected[target].mean()),
                "opposite_return": float(opposite[target].mean()),
                "spread": float(selected[target].mean() - opposite[target].mean()),
                "ic": float(frame[feature].corr(frame[target], method="spearman")),
            })
        method = f"每个 5 分钟时点在 {len(requested_symbols) or len(available_symbols)} 只证券中按特征分位分组"
    else:
        # Single-security idea: in-sample time-series quantiles are explicitly
        # labelled exploratory because the threshold uses this day's distribution.
        for _, frame in sample_frame.groupby("Symbol"):
            if len(frame) < 10 or frame[feature].nunique() < 2:
                continue
            ranked = frame.sort_values(feature)
            count = max(1, int(np.ceil(len(ranked) * quantile)))
            low, high = ranked.head(count), ranked.tail(count)
            selected, opposite = (high, low) if direction == "high" else (low, high)
            selected_signal_returns.extend(selected[target].astype(float).tolist())
            measurements.append({
                "selected_return": float(selected[target].mean()),
                "opposite_return": float(opposite[target].mean()),
                "spread": float(selected[target].mean() - opposite[target].mean()),
                "ic": float(frame[feature].corr(frame[target], method="spearman")),
            })
        method = "个股日内时间序列分位比较；分位阈值使用全日分布，仅作探索"

    if not measurements:
        raise ValueError("有效样本不足，暂时无法评估这个设定")
    stats = pd.DataFrame(measurements)
    mean_selected = float(stats.selected_return.mean())
    mean_spread = float(stats.spread.mean())
    positive_selected = float(np.mean(np.asarray(selected_signal_returns) > 0))
    positive_spread = float((stats.spread > 0).mean()) if cross_sectional else None
    edge_profit_share = float((stats.spread * 10_000 > cost_bps).mean()) if cross_sectional else None
    selected_profit_share = float(np.mean(np.asarray(selected_signal_returns) * 10_000 > cost_bps))
    break_even_cost = mean_spread * 10_000 if cross_sectional else mean_selected * 10_000
    net_edge = break_even_cost - cost_bps
    if len(sample_frame) < 30 or (cross_sectional and len(measurements) < 10):
        assessment = "当前有效样本偏少，暂不形成方向线索"
    elif not cross_sectional and mean_selected * 10_000 > cost_bps and selected_profit_share >= 0.55:
        assessment = "该股单日出现正向关联线索，值得跨日复核"
    elif not cross_sectional:
        assessment = "该股单日方向或成本敏感性不稳定，可调整假设继续研究"
    elif mean_spread * 10_000 > cost_bps and edge_profit_share >= 0.55:
        assessment = "单日高低组呈正向关联线索，值得跨日复核"
    elif mean_spread * 10_000 > cost_bps:
        assessment = "均值高于成本情景线，但时段表现不稳定"
    elif mean_spread > 0:
        assessment = "方向均值微正，对成本假设敏感"
    elif mean_spread < 0 and positive_spread <= 0.45:
        assessment = "当前高低组方向偏弱，可将反向关系作为研究假设"
    else:
        assessment = "单日方向不稳定，当前证据较弱"
    return {
        "idea": idea,
        "feature": feature,
        "feature_label": known[feature]["label"],
        "direction": direction,
        "direction_label": "高特征值组" if direction == "high" else "低特征值组",
        "horizon_minutes": horizon,
        "quantile": quantile,
        "assumed_total_cost_bps": cost_bps,
        "symbols": requested_symbols or sorted(available_symbols),
        "scope_mode": "cross_sectional" if cross_sectional else "single_stock_time_series",
        "method": method,
        "assessment": assessment,
        "sample_count": int(len(sample_frame)),
        "periods_or_symbols": int(len(measurements) if cross_sectional else len(sample_frame)),
        "mean_selected_return_bps": round(mean_selected * 10_000, 3),
        "selected_positive_share": round(positive_selected, 4),
        "selected_net_return_bps": round(mean_selected * 10_000 - cost_bps, 3),
        "selected_net_positive_share": round(selected_profit_share, 4),
        "mean_selected_minus_opposite_bps": round(mean_spread * 10_000, 3),
        "outperform_share": round(positive_spread, 4) if cross_sectional else None,
        "net_edge_after_cost_bps": round(net_edge, 3),
        "edge_exceeds_cost_share": round(edge_profit_share, 4) if cross_sectional else None,
        "break_even_total_cost_bps": round(break_even_cost, 3),
        "mean_rank_ic": round(float(stats.ic.mean()), 4),
        "caveat": "单日结果用于发现关联方向，不足以判断策略能否赚钱。成本输入只是敏感性情景；组间收益差未约束成交容量、滑点和冲击成本。应在更多交易日形成样本外假设后再做完整回测。",
    }


class MarketData:
    def __init__(self) -> None:
        if not L5_PATH.exists() or not TRADES_PATH.exists():
            raise FileNotFoundError(
                "Simulation Parquet outputs are missing. Run `python simulate_exchange.py` first."
            )
        symbols_frame = pd.read_parquet(L5_PATH, columns=["Symbol", "Time"])
        self.symbols = sorted(int(value) for value in symbols_frame.Symbol.unique())
        self.timeline = sorted(int(value) for value in symbols_frame[symbols_frame.Symbol == self.symbols[0]].Time.unique())
        self.books: dict[int, pd.DataFrame] = {}
        self.trade_frames: dict[int, pd.DataFrame] = {}
        self.candle_frames: dict[tuple[int, int], list[dict]] = {}
        self.lock = threading.RLock()

    def book_frame(self, symbol: int) -> pd.DataFrame:
        with self.lock:
            if symbol not in self.books:
                frame = pd.read_parquet(L5_PATH, filters=[("Symbol", "==", symbol)])
                frame = frame.sort_values("Time", kind="stable").reset_index(drop=True)
                self.books[symbol] = frame
            return self.books[symbol]

    def trade_frame(self, symbol: int) -> pd.DataFrame:
        with self.lock:
            if symbol not in self.trade_frames:
                frame = pd.read_parquet(
                    TRADES_PATH,
                    columns=["BizIndex", "Symbol", "Time", "Price", "PriceTicks", "Volume", "BSFlag", "AggressorSide", "Session"],
                    filters=[("Symbol", "==", symbol)],
                )
                frame = frame.sort_values(["Time", "BizIndex"], kind="stable").reset_index(drop=True)
                self.trade_frames[symbol] = frame
            return self.trade_frames[symbol]

    def book_at(self, symbol: int, raw_time: int) -> dict:
        frame = self.book_frame(symbol)
        times = frame.Time.to_numpy(dtype=np.int64, copy=False)
        index = int(np.searchsorted(times, raw_time, side="right") - 1)
        if index < 0:
            return {"Time": raw_time, "Symbol": symbol, "Session": "before_open"}
        row = frame.iloc[index]
        bids = [
            {"price": float(row[f"BidPrice{i}"]), "volume": int(row[f"BidVolume{i}"])}
            for i in range(1, 6)
            if float(row[f"BidPrice{i}"]) > 0 and int(row[f"BidVolume{i}"]) > 0
        ]
        asks = [
            {"price": float(row[f"AskPrice{i}"]), "volume": int(row[f"AskVolume{i}"])}
            for i in range(1, 6)
            if float(row[f"AskPrice{i}"]) > 0 and int(row[f"AskVolume{i}"]) > 0
        ]
        return {"Time": int(row.Time), "Symbol": symbol, "Session": str(row.Session), "bids": bids, "asks": asks}

    def recent_trades(self, symbol: int, raw_time: int, count: int = 60) -> list[dict]:
        frame = self.trade_frame(symbol)
        times = frame.Time.to_numpy(dtype=np.int64, copy=False)
        end = int(np.searchsorted(times, raw_time, side="right"))
        start = max(0, end - count)
        rows = frame.iloc[start:end]
        result = []
        for row in rows.itertuples(index=False):
            result.append({
                "biz_index": int(row.BizIndex),
                "time": int(row.Time),
                "time_text": raw_time_text(int(row.Time)),
                "price": float(row.Price),
                "volume": int(row.Volume),
                "aggressor": str(row.AggressorSide),
                "session": str(row.Session),
            })
        result.reverse()
        return result

    def trades_between(self, symbol: int, old_time: int, new_time: int) -> pd.DataFrame:
        frame = self.trade_frame(symbol)
        times = frame.Time.to_numpy(dtype=np.int64, copy=False)
        left = int(np.searchsorted(times, old_time, side="right"))
        right = int(np.searchsorted(times, new_time, side="right"))
        return frame.iloc[left:right]

    def last_trade_price(self, symbol: int, raw_time: int) -> float | None:
        frame = self.trade_frame(symbol)
        times = frame.Time.to_numpy(dtype=np.int64, copy=False)
        index = int(np.searchsorted(times, raw_time, side="right") - 1)
        if index < 0:
            return None
        return float(frame.iloc[index].Price)

    @staticmethod
    def _raw_time_from_seconds(seconds: int) -> int:
        hour, remainder = divmod(int(seconds), 3600)
        minute, second = divmod(remainder, 60)
        return hour * 10_000_000 + minute * 100_000 + second * 1_000

    def candles(self, symbol: int, interval_minutes: int) -> list[dict]:
        if interval_minutes not in (1, 5):
            raise ValueError("K 线周期只支持 1 分钟或 5 分钟")
        key = (symbol, interval_minutes)
        with self.lock:
            cached = self.candle_frames.get(key)
            if cached is not None:
                return cached

        frame = self.trade_frame(symbol)
        # The web replay ends at 15:00. Post-close fixed-price prints are not
        # part of the intraday auction/continuous-session candles here.
        frame = frame.loc[frame.Time <= 150_000_000, ["Time", "Price", "Volume", "Session"]].copy()
        if frame.empty:
            result: list[dict] = []
        else:
            raw = frame.Time.to_numpy(dtype=np.int64, copy=False)
            hours = raw // 10_000_000
            minutes = (raw // 100_000) % 100
            seconds = (raw // 1_000) % 100
            frame["trade_seconds"] = hours * 3600 + minutes * 60 + seconds
            interval_seconds = interval_minutes * 60
            frame["start_seconds"] = (frame.trade_seconds // interval_seconds) * interval_seconds
            opening_auction = frame.Session.eq("opening_auction")
            frame.loc[opening_auction, "start_seconds"] = 9 * 3600 + 15 * 60
            close_auction = frame.Session.eq("closing_auction")
            close_start = 14 * 3600 + 57 * 60
            frame.loc[close_auction, "start_seconds"] = close_start
            grouped = frame.groupby(["Session", "start_seconds"], sort=True, as_index=False).agg(
                Open=("Price", "first"),
                High=("Price", "max"),
                Low=("Price", "min"),
                Close=("Price", "last"),
                Volume=("Volume", "sum"),
                TradeCount=("Volume", "size"),
            )
            result = []
            for row in grouped.itertuples(index=False):
                start_seconds = int(row.start_seconds)
                session = str(row.Session)
                if session == "opening_auction":
                    session_cutoff = 9 * 3600 + 25 * 60
                elif session == "closing_auction":
                    session_cutoff = 15 * 3600
                elif session == "continuous" and start_seconds < 12 * 3600:
                    session_cutoff = 11 * 3600 + 30 * 60
                elif session == "continuous":
                    session_cutoff = 14 * 3600 + 57 * 60
                else:
                    session_cutoff = start_seconds + interval_seconds
                if session in ("opening_auction", "closing_auction"):
                    end_seconds = session_cutoff
                else:
                    end_seconds = min(start_seconds + interval_seconds, session_cutoff)
                result.append({
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "time": self._raw_time_from_seconds(start_seconds),
                    "session": session,
                    "open": float(row.Open),
                    "high": float(row.High),
                    "low": float(row.Low),
                    "close": float(row.Close),
                    "volume": int(row.Volume),
                    "trade_count": int(row.TradeCount),
                })
            result.sort(key=lambda row: (row["start_seconds"], row["session"]))

        with self.lock:
            return self.candle_frames.setdefault(key, result)


class SimAccount:
    def __init__(self, market: MarketData) -> None:
        self.market = market
        self.current_index = self._index_for(93_000_000)
        self.cash = INITIAL_CASH
        self.positions: dict[int, dict[str, float | int]] = {}
        self.orders: dict[str, dict] = {}
        self.fills: list[dict] = []
        self.queue_ahead: dict[tuple[int, int, int], int] = {}
        self.sequence = 0
        self.lock = threading.RLock()

    def _index_for(self, raw_time: int) -> int:
        return max(0, bisect.bisect_right(self.market.timeline, raw_time) - 1)

    @property
    def current_time(self) -> int:
        return self.market.timeline[self.current_index]

    def _open_orders(self, symbol: int | None = None) -> list[dict]:
        return [
            order for order in self.orders.values()
            if order["remaining"] > 0 and order["status"] in ("OPEN", "PARTIAL")
            and (symbol is None or order["symbol"] == symbol)
        ]

    def _reserved_cash(self) -> float:
        return sum(
            order["remaining"] * order["price"]
            for order in self._open_orders()
            if order["side"] == "BUY"
        )

    def _reserved_shares(self, symbol: int) -> int:
        return sum(
            int(order["remaining"])
            for order in self._open_orders(symbol)
            if order["side"] == "SELL"
        )

    def _position(self, symbol: int) -> dict[str, float | int]:
        return self.positions.setdefault(symbol, {"quantity": 0, "average_cost": 0.0, "realized_pnl": 0.0})

    def _apply_fill(self, order: dict, quantity: int, price: float, raw_time: int, reason: str) -> None:
        if quantity <= 0:
            return
        position = self._position(order["symbol"])
        notional = quantity * price
        if order["side"] == "BUY":
            old_quantity = int(position["quantity"])
            old_cost = float(position["average_cost"])
            new_quantity = old_quantity + quantity
            position["average_cost"] = (old_quantity * old_cost + notional) / new_quantity
            position["quantity"] = new_quantity
            self.cash -= notional
        else:
            old_quantity = int(position["quantity"])
            sold = min(old_quantity, quantity)
            position["realized_pnl"] = float(position["realized_pnl"]) + (price - float(position["average_cost"])) * sold
            position["quantity"] = old_quantity - sold
            if int(position["quantity"]) == 0:
                position["average_cost"] = 0.0
            self.cash += notional
        fill = {
            "fill_id": f"F{len(self.fills) + 1:07d}",
            "order_id": order["order_id"],
            "symbol": order["symbol"],
            "side": order["side"],
            "price": round(price, 4),
            "quantity": quantity,
            "time": raw_time,
            "time_text": raw_time_text(raw_time),
            "reason": reason,
        }
        self.fills.append(fill)
        if len(self.fills) > 2_000:
            self.fills = self.fills[-2_000:]

    def _match_historical_trade(self, symbol: int, trade: Any) -> None:
        trade_time = int(trade.Time)
        trade_price_ticks = int(trade.PriceTicks)
        trade_price = float(trade.Price)
        bs_flag = int(trade.BSFlag)
        sides: list[int] = []
        if bs_flag == 83:  # sell aggressor can fill our resting buy
            sides = [1]
        elif bs_flag == 66:  # buy aggressor can fill our resting sell
            sides = [-1]
        else:
            # Auction prints do not identify a continuous aggressor/queue order.
            # Do not invent simulated fills from their aggregate allocation.
            return

        for side in sides:
            available = int(trade.Volume)
            key = (symbol, side, trade_price_ticks)
            open_at_price = sorted(
                [order for order in self._open_orders(symbol) if order["side_code"] == side and order["price_ticks"] == trade_price_ticks],
                key=lambda order: order["sequence"],
            )
            if not open_at_price:
                continue
            ahead = self.queue_ahead.get(key, 0)
            consumed = min(ahead, available)
            self.queue_ahead[key] = ahead - consumed
            available -= consumed
            for order in open_at_price:
                if available <= 0:
                    break
                quantity = min(int(order["remaining"]), available)
                order["remaining"] -= quantity
                order["status"] = "FILLED" if order["remaining"] == 0 else "PARTIAL"
                self._apply_fill(order, quantity, trade_price, trade_time, "historical_trade")
                available -= quantity

    def _reset_ledger(self) -> None:
        self.cash = INITIAL_CASH
        self.positions.clear()
        self.orders.clear()
        self.fills.clear()
        self.queue_ahead.clear()
        self.sequence = 0

    def seek(self, index: int) -> bool:
        index = max(0, min(int(index), len(self.market.timeline) - 1))
        with self.lock:
            old_time = self.current_time
            new_time = self.market.timeline[index]
            reset = new_time < old_time
            if reset:
                self._reset_ledger()
            elif new_time > old_time:
                open_symbols = sorted({order["symbol"] for order in self._open_orders()})
                for symbol in open_symbols:
                    for trade in self.market.trades_between(symbol, old_time, new_time).itertuples(index=False):
                        self._match_historical_trade(symbol, trade)
            self.current_index = index
            return reset

    def place_limit(self, symbol: int, side: str, price: float, quantity: int) -> dict:
        side = side.upper()
        if symbol not in self.market.symbols:
            raise ValueError("未知证券代码")
        if side not in ("BUY", "SELL"):
            raise ValueError("方向必须为 BUY 或 SELL")
        if quantity <= 0 or quantity % LOT_SIZE:
            raise ValueError("A 股模拟委托数量必须为正数且为 100 股整数倍")
        ticks = price_ticks(price)
        if ticks <= 0 or ticks % 100:
            raise ValueError("限价必须大于 0 且符合 0.01 元价格档位")
        price = ticks / PRICE_SCALE
        with self.lock:
            if side == "BUY" and quantity * price > self.cash - self._reserved_cash() + 1e-8:
                raise ValueError("可用资金不足（按委托限价预留）")
            if side == "SELL":
                available = int(self._position(symbol)["quantity"]) - self._reserved_shares(symbol)
                if quantity > available:
                    raise ValueError(f"可卖持仓不足：可用 {available} 股")

            self.sequence += 1
            order_id = f"SIM{self.sequence:07d}"
            side_code = 1 if side == "BUY" else -1
            order = {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "side_code": side_code,
                "price": price,
                "price_ticks": ticks,
                "original_quantity": int(quantity),
                "remaining": int(quantity),
                "status": "OPEN",
                "created_time": self.current_time,
                "created_time_text": raw_time_text(self.current_time),
                "sequence": self.sequence,
            }
            self.orders[order_id] = order

            book = self.market.book_at(symbol, self.current_time)
            contra = book.get("asks" if side == "BUY" else "bids", [])
            for level in contra:
                level_price = float(level["price"])
                crosses = level_price <= price if side == "BUY" else level_price >= price
                if not crosses or order["remaining"] <= 0:
                    continue
                quantity_here = min(int(order["remaining"]), int(level["volume"]))
                if quantity_here <= 0:
                    continue
                order["remaining"] -= quantity_here
                self._apply_fill(order, quantity_here, level_price, self.current_time, "cross_current_l5")
            if order["remaining"] == 0:
                order["status"] = "FILLED"
            else:
                order["status"] = "PARTIAL" if order["remaining"] < order["original_quantity"] else "OPEN"
                key = (symbol, side_code, ticks)
                if key not in self.queue_ahead:
                    same_side = book.get("bids" if side == "BUY" else "asks", [])
                    self.queue_ahead[key] = sum(
                        int(level["volume"]) for level in same_side
                        if price_ticks(level["price"]) == ticks
                    )
            return self._public_order(order)

    def cancel(self, order_id: str) -> dict:
        with self.lock:
            order = self.orders.get(order_id)
            if order is None or order["remaining"] <= 0 or order["status"] not in ("OPEN", "PARTIAL"):
                raise ValueError("委托不存在或已结束")
            order["status"] = "CANCELLED"
            order["remaining"] = 0
            return self._public_order(order)

    @staticmethod
    def _public_order(order: dict) -> dict:
        return {
            key: order[key]
            for key in ["order_id", "symbol", "side", "price", "original_quantity", "remaining", "status", "created_time_text"]
        }

    def state(self, symbol: int) -> dict:
        with self.lock:
            raw_time = self.current_time
            book = self.market.book_at(symbol, raw_time)
            recent = self.market.recent_trades(symbol, raw_time)
            positions = []
            for position_symbol, position in sorted(self.positions.items()):
                last = self.market.last_trade_price(position_symbol, raw_time)
                mark = float(last) if last is not None else float(position["average_cost"])
                quantity = int(position["quantity"])
                positions.append({
                    "symbol": position_symbol,
                    "quantity": quantity,
                    "available_quantity": quantity - self._reserved_shares(position_symbol),
                    "average_cost": round(float(position["average_cost"]), 4),
                    "last_price": round(mark, 4),
                    "market_value": round(quantity * mark, 2),
                    "unrealized_pnl": round((mark - float(position["average_cost"])) * quantity, 2),
                    "realized_pnl": round(float(position["realized_pnl"]), 2),
                })
            position = self.positions.get(symbol, {"quantity": 0, "average_cost": 0.0, "realized_pnl": 0.0})
            last_price = self.market.last_trade_price(symbol, raw_time)
            current_price = float(last_price) if last_price is not None else (float(position["average_cost"]) or 0.0)
            open_orders = [self._public_order(order) for order in self._open_orders()]
            fills = list(reversed(self.fills[-50:]))
            reserved_cash = self._reserved_cash()
            total_market_value = sum(row["market_value"] for row in positions)
            total_unrealized = sum(row["unrealized_pnl"] for row in positions)
            return {
                "symbol": symbol,
                "time": raw_time,
                "time_text": raw_time_text(raw_time),
                "index": self.current_index,
                "timeline_length": len(self.market.timeline),
                "progress": self.current_index / max(1, len(self.market.timeline) - 1),
                "market": {
                    **book,
                    "last_price": round(current_price, 4),
                    "recent_trades": recent,
                },
                "account": {
                    "initial_cash": INITIAL_CASH,
                    "cash": round(self.cash, 2),
                    "reserved_cash": round(reserved_cash, 2),
                    "available_cash": round(self.cash - reserved_cash, 2),
                    "total_market_value": round(total_market_value, 2),
                    "equity": round(self.cash + total_market_value, 2),
                    "unrealized_pnl": round(total_unrealized, 2),
                    "positions": positions,
                    "current_position": {
                        "quantity": int(position["quantity"]),
                        "available_quantity": int(position["quantity"]) - self._reserved_shares(symbol),
                        "average_cost": round(float(position["average_cost"]), 4),
                        "realized_pnl": round(float(position["realized_pnl"]), 2),
                    },
                },
                "open_orders": open_orders,
                "fills": fills,
            }


class WebHandler(BaseHTTPRequestHandler):
    server_version = "SSE50Replay/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[web] " + fmt % args)

    @property
    def app(self) -> tuple[MarketData, SimAccount]:
        return self.server.app  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        market, account = self.app
        if parsed.path == "/":
            self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif parsed.path == "/research":
            self._send(200, (WEB_DIR / "research.html").read_bytes(), "text/html; charset=utf-8")
        elif parsed.path in ("/app.js", "/style.css"):
            name = "app.js" if parsed.path.endswith("app.js") else "style.css"
            content_type = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
            self._send(200, (WEB_DIR / name).read_bytes(), content_type)
        elif parsed.path in ("/research.js", "/research.css"):
            name = parsed.path.lstrip("/")
            content_type = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
            self._send(200, (WEB_DIR / name).read_bytes(), content_type)
        elif parsed.path.startswith("/download/"):
            dataset = parsed.path.removeprefix("/download/")
            path = L2_DATASETS.get(dataset)
            if path is None or not path.exists():
                self._json(404, {"error": "未知的 L2 数据集"})
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.end_headers()
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
        elif parsed.path == "/api/l2-rows":
            query = parse_qs(parsed.query)
            dataset = query.get("dataset", ["ord"])[0]
            try:
                symbol = int(query.get("symbol", [market.symbols[0]])[0])
                offset = max(0, int(query.get("offset", ["0"])[0]))
                limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
                start_time = raw_time_bound(query.get("start", [""])[0])
                end_time = raw_time_bound(query.get("end", [""])[0])
            except (TypeError, ValueError):
                self._json(400, {"error": "证券代码、时间或分页参数格式错误"})
                return
            if dataset not in L2_DATASETS:
                self._json(400, {"error": "数据类型只支持 ord、exe、snp"})
            elif symbol not in market.symbols:
                self._json(400, {"error": "未知证券代码"})
            else:
                frame = l2_symbol_frame(dataset, symbol)
                selected = frame
                if start_time is not None:
                    selected = selected.loc[selected.Time.ge(start_time)]
                if end_time is not None:
                    selected = selected.loc[selected.Time.le(end_time)]
                rows = selected.iloc[offset:offset + limit]
                self._json(200, {
                    "dataset": dataset, "symbol": symbol, "columns": list(frame.columns),
                    "total": int(len(selected)), "offset": offset, "limit": limit,
                    "rows": json_safe_records(rows),
                })
        elif parsed.path == "/api/research":
            report_path = OUTPUT / "l2_research_20260923.json"
            if report_path.exists():
                self._send(200, report_path.read_bytes(), "application/json; charset=utf-8")
            else:
                self._json(404, {"error": "分析结果不存在，请先运行 python generate_l2_research.py"})
        elif parsed.path == "/api/meta":
            self._json(200, {
                "date": "2026-09-23",
                "symbols": market.symbols,
                "timeline": market.timeline,
                "default_symbol": market.symbols[0],
                "current_index": account.current_index,
                "initial_cash": INITIAL_CASH,
                "lot_size": LOT_SIZE,
            })
        elif parsed.path == "/api/state":
            query = parse_qs(parsed.query)
            symbol = int(query.get("symbol", [market.symbols[0]])[0])
            if symbol not in market.symbols:
                self._json(400, {"error": "未知证券代码"})
            else:
                self._json(200, account.state(symbol))
        elif parsed.path == "/api/candles":
            query = parse_qs(parsed.query)
            symbol = int(query.get("symbol", [market.symbols[0]])[0])
            interval = int(query.get("interval", ["1"])[0])
            if symbol not in market.symbols:
                self._json(400, {"error": "未知证券代码"})
            elif interval not in (1, 5):
                self._json(400, {"error": "K 线周期只支持 1 分钟或 5 分钟"})
            else:
                self._json(200, {"symbol": symbol, "interval": interval, "candles": market.candles(symbol, interval)})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        market, account = self.app
        try:
            body = self._body()
            if self.path == "/api/strategy-evaluate":
                self._json(200, evaluate_strategy_idea(body))
            elif self.path == "/api/seek":
                index = int(body["index"])
                symbol = int(body.get("symbol", market.symbols[0]))
                if symbol not in market.symbols:
                    raise ValueError("未知证券代码")
                reset = account.seek(index)
                state = account.state(symbol)
                state["ledger_reset"] = reset
                self._json(200, state)
            elif self.path == "/api/order":
                order = account.place_limit(
                    int(body["symbol"]), str(body["side"]), float(body["price"]), int(body["quantity"])
                )
                self._json(200, {"order": order, "state": account.state(int(body["symbol"]))})
            elif self.path == "/api/cancel":
                order = account.cancel(str(body["order_id"]))
                self._json(200, {"order": order, "state": account.state(int(body["symbol"]))})
            elif self.path == "/api/reset":
                symbol = int(body.get("symbol", market.symbols[0]))
                if symbol not in market.symbols:
                    raise ValueError("未知证券代码")
                with account.lock:
                    account._reset_ledger()
                self._json(200, account.state(symbol))
            else:
                self._json(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # return a useful UI error without dumping a traceback to the browser
            print(f"[web] request failed: {exc!r}")
            self._json(500, {"error": "服务器处理失败"})


def main() -> None:
    market = MarketData()
    app = SimAccount(market)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), WebHandler)
    server.app = (market, app)  # type: ignore[attr-defined]
    print(f"SSE50 模拟交易网页： http://127.0.0.1:8765", flush=True)
    print("按 Ctrl+C 停止。模拟账户仅保存在当前进程内。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
