#!/usr/bin/env python3
"""Create a reproducible exploratory summary for the 2026-09-23 SSE L2 sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "exchange_simulation_output" / "l2_research_20260923.json"
DATE = "2026-09-23"
BUY = 66
SELL = 83
ADD = 65
DELETE = 68


def raw_to_millis(raw: pd.Series) -> pd.Series:
    value = raw.astype("int64")
    hour = value // 10_000_000
    minute = value // 100_000 % 100
    second = value // 1_000 % 100
    millis = value % 1_000
    return hour * 3_600_000 + minute * 60_000 + second * 1_000 + millis


def seconds_to_raw(seconds: int) -> int:
    hour, remainder = divmod(int(seconds), 3600)
    minute, second = divmod(remainder, 60)
    return hour * 10_000_000 + minute * 100_000 + second * 1_000


def time_label(seconds: int) -> str:
    hour, remainder = divmod(int(seconds), 3600)
    minute, _ = divmod(remainder, 60)
    return f"{hour:02d}:{minute:02d}"


def safe_float(value: Any, digits: int = 6) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    result = []
    for record in frame.to_dict("records"):
        clean = {}
        for key, value in record.items():
            if pd.isna(value):
                clean[key] = None
            else:
                clean[key] = value.item() if isinstance(value, np.generic) else value
        result.append(clean)
    return result


def build_strategy_screen(directional: pd.DataFrame, order_events: pd.DataFrame, snap: pd.DataFrame) -> dict[str, Any]:
    """One-day cross-sectional screen of L2 features against the next 5m mid return."""
    trades = directional.copy()
    trades["bucket_seconds"] = (trades.trade_millis // 300_000 * 300).astype("int64")
    trades["signed_amount"] = np.where(trades.BSFlag.eq(BUY), trades.amount, -trades.amount)
    trades["large_amount"] = np.where(trades.amount.ge(100_000), trades.amount, 0.0)
    trade_features = trades.groupby(["Symbol", "bucket_seconds"]).agg(
        trade_turnover=("amount", "sum"), signed_trade_amount=("signed_amount", "sum"),
        large_turnover=("large_amount", "sum"), trade_count=("Volume", "size"),
    )
    trade_features["aggressive_flow_imbalance"] = trade_features.signed_trade_amount / trade_features.trade_turnover.replace(0, np.nan)
    trade_features["large_trade_turnover_share"] = trade_features.large_turnover / trade_features.trade_turnover.replace(0, np.nan)

    orders = order_events.copy()
    orders["bucket_seconds"] = (raw_to_millis(orders.Time) // 300_000 * 300).astype("int64")
    orders["signed_volume"] = orders.Volume * np.where(orders.FunctionCode.eq(BUY), 1, -1)
    adds = orders.loc[orders.OrderKind.eq(ADD)].groupby(["Symbol", "bucket_seconds"]).agg(
        add_volume=("Volume", "sum"), add_signed_volume=("signed_volume", "sum")
    )
    cancels = orders.loc[orders.OrderKind.eq(DELETE)].groupby(["Symbol", "bucket_seconds"]).agg(
        cancel_volume=("Volume", "sum"), cancel_signed_volume=("signed_volume", "sum")
    )
    order_features = adds.join(cancels, how="outer").fillna(0)
    order_features["add_order_imbalance"] = order_features.add_signed_volume / order_features.add_volume.replace(0, np.nan)
    order_features["cancel_add_volume_ratio"] = order_features.cancel_volume / order_features.add_volume.replace(0, np.nan)

    quotes = snap.sort_values(["Symbol", "Time"], kind="stable").copy()
    quotes["bucket_seconds"] = (raw_to_millis(quotes.Time) // 300_000 * 300).astype("int64")
    quotes["mid"] = (quotes.AskPrice1.astype("float64") + quotes.BidPrice1.astype("float64")) / 2
    quote_features = quotes.groupby(["Symbol", "bucket_seconds"]).agg(
        mid=("mid", "last"), spread_bps=("spread_bps", "last"), book_imbalance5=("depth_imbalance5", "last")
    )
    panel = trade_features.join(order_features, how="outer").join(quote_features, how="left").reset_index()
    panel = panel.sort_values(["Symbol", "bucket_seconds"], kind="stable")
    for horizon in (5, 10, 15):
        steps = horizon // 5
        future_mid = panel.groupby("Symbol").mid.shift(-steps)
        future_bucket = panel.groupby("Symbol").bucket_seconds.shift(-steps)
        column = f"forward_{horizon}m_return"
        panel[column] = future_mid / panel.mid - 1
        panel.loc[future_bucket - panel.bucket_seconds != horizon * 60, column] = np.nan

    feature_specs = [
        ("aggressive_flow_imbalance", "主动买卖成交额差", "成交方向额差 / 总成交额；衡量短时主动买卖压力。"),
        ("large_trade_turnover_share", "大额成交占比", "≥10 万元成交额 / 总成交额；观察较大单笔对短时价格的影响。"),
        ("book_imbalance5", "L5 盘口深度失衡", "五档买盘金额与卖盘金额之差 / 合计；正值表示买盘深度较大。"),
        ("add_order_imbalance", "新增委托方向失衡", "买卖新增委托股数净差 / 新增总股数。"),
        ("cancel_add_volume_ratio", "撤单/新增量比", "同一 5 分钟桶内撤单股数 / 新增委托股数。"),
        ("spread_bps", "买卖价差", "五分钟桶末盘口价差 / 中间价，以基点计。"),
    ]
    measurements = []
    for feature, label, description in feature_specs:
        rows = []
        for bucket, frame in panel.groupby("bucket_seconds"):
            valid = frame[[feature, "forward_5m_return"]].replace([np.inf, -np.inf], np.nan).dropna()
            if len(valid) < 10 or valid[feature].nunique() < 2:
                continue
            ic = valid[feature].corr(valid.forward_5m_return, method="spearman")
            rank = valid[feature].rank(method="average", pct=True)
            bottom = valid.loc[rank <= .2, "forward_5m_return"].mean()
            top = valid.loc[rank >= .8, "forward_5m_return"].mean()
            rows.append({"bucket": int(bucket), "ic": ic, "spread": top - bottom})
        values = pd.DataFrame(rows)
        measurements.append({
            "feature": feature,
            "label": label,
            "description": description,
            "valid_periods": int(len(values)),
            "mean_rank_ic": safe_float(values.ic.mean() if len(values) else None, 4),
            "positive_ic_period_share": safe_float((values.ic > 0).mean() if len(values) else None, 4),
            "mean_top_minus_bottom_bps": safe_float(values.spread.mean() * 10_000 if len(values) else None, 2),
            "positive_spread_period_share": safe_float((values.spread > 0).mean() if len(values) else None, 4),
        })
    measurements.sort(key=lambda x: abs(x["mean_top_minus_bottom_bps"] or 0), reverse=True)
    sample_columns = ["Symbol", "bucket_seconds", "aggressive_flow_imbalance", "large_trade_turnover_share",
                      "book_imbalance5", "add_order_imbalance", "cancel_add_volume_ratio", "spread_bps",
                      "forward_5m_return", "forward_10m_return", "forward_15m_return"]
    samples = panel[sample_columns].copy()
    samples["time"] = samples.bucket_seconds.map(time_label)
    return {
        "target": "每只股票每个 5 分钟信号桶结束后的下一 5 分钟中间价收益；横截面计算 Rank IC 与最高/最低 20% 组收益差。",
        "features": measurements,
        "samples": json_records(samples.round(8)),
        "limits": "这是单日、50 只上证50样本的探索性筛查；收益差未扣手续费、冲击成本和可成交约束，也未作跨日/样本外验证。只能用于挑选后续研究方向，不能据此认定可盈利。",
    }


def build_report(orders_path: Path, trades_path: Path, snapshots_path: Path) -> dict[str, Any]:
    orders = pd.read_parquet(orders_path)
    trades = pd.read_parquet(trades_path)
    snapshots = pd.read_parquet(snapshots_path)

    symbols = sorted(int(value) for value in set(orders.Symbol.unique()) | set(trades.Symbol.unique()) | set(snapshots.Symbol.unique()))

    trades["amount"] = trades.Price.astype("float64") * trades.Volume.astype("float64")
    trades["trade_seconds"] = (
        trades.Time // 10_000_000 * 3600
        + trades.Time // 100_000 % 100 * 60
        + trades.Time // 1_000 % 100
    ).astype("int64")
    trades["trade_millis"] = raw_to_millis(trades.Time)

    opening = trades.Time.between(91_500_000, 92_500_000, inclusive="both")
    morning = trades.Time.ge(93_000_000) & trades.Time.lt(113_000_000)
    afternoon = trades.Time.ge(130_000_000) & trades.Time.lt(145_700_000)
    closing = trades.Time.ge(145_700_000) & trades.Time.le(150_000_000)
    post_close = trades.Time.gt(150_000_000) & trades.Time.le(153_000_000)
    continuous = trades.loc[morning | afternoon].copy()
    directional = continuous.loc[continuous.BSFlag.isin([BUY, SELL])].copy()
    auction = trades.loc[opening | closing | (trades.BSFlag == 0)].copy()
    after = trades.loc[post_close].copy()

    order_events = orders.loc[
        orders.FunctionCode.isin([BUY, SELL]) & orders.OrderKind.isin([ADD, DELETE])
    ].copy()
    order_events["amount"] = order_events.Price.astype("float64") * order_events.Volume.astype("float64")
    order_adds = order_events.loc[order_events.OrderKind.eq(ADD)]
    order_cancels = order_events.loc[order_events.OrderKind.eq(DELETE)]

    snap = snapshots.loc[
        snapshots.Time.ge(93_000_000) & snapshots.Time.lt(145_700_000)
    ].copy()
    mid = (snap.AskPrice1.astype("float64") + snap.BidPrice1.astype("float64")) / 2
    spread = snap.AskPrice1.astype("float64") - snap.BidPrice1.astype("float64")
    snap["spread_bps"] = spread / mid.replace(0, np.nan) * 10_000
    bid_value = sum(snap[f"BidPrice{i}"].astype("float64") * snap[f"BidVolume{i}"].astype("float64") for i in range(1, 6))
    ask_value = sum(snap[f"AskPrice{i}"].astype("float64") * snap[f"AskVolume{i}"].astype("float64") for i in range(1, 6))
    snap["depth_imbalance5"] = (bid_value - ask_value) / (bid_value + ask_value).replace(0, np.nan)
    snap["depth_value5"] = bid_value + ask_value

    # Per-security derived measures, all restricted to continuous trading where
    # a directional trade or a meaningful two-sided quote is available.
    directional["large_ticket"] = directional.amount.ge(100_000)
    directional["signed_amount"] = np.where(directional.BSFlag.eq(BUY), directional.amount, -directional.amount)
    trade_by_symbol = directional.groupby("Symbol").agg(
        trade_count=("Volume", "size"), trade_volume=("Volume", "sum"),
        turnover=("amount", "sum"), median_trade_amount=("amount", "median"),
        p90_trade_amount=("amount", lambda values: values.quantile(.90)),
        large_trade_count=("large_ticket", "sum"), large_trade_turnover=("amount", lambda values: values[directional.loc[values.index, "large_ticket"]].sum()),
        signed_trade_amount=("signed_amount", "sum"),
    )
    trade_by_symbol["large_trade_count_share"] = trade_by_symbol.large_trade_count / trade_by_symbol.trade_count.replace(0, np.nan)
    trade_by_symbol["large_trade_turnover_share"] = trade_by_symbol.large_trade_turnover / trade_by_symbol.turnover.replace(0, np.nan)
    trade_by_symbol["trade_flow_imbalance"] = trade_by_symbol.signed_trade_amount / trade_by_symbol.turnover.replace(0, np.nan)
    side_turnover = directional.pivot_table(
        index="Symbol", columns="BSFlag", values="amount", aggfunc="sum", fill_value=0
    ).rename(columns={BUY: "active_buy_turnover", SELL: "active_sell_turnover"})
    for column in ["active_buy_turnover", "active_sell_turnover"]:
        if column not in side_turnover:
            side_turnover[column] = 0.0
    trade_by_symbol = trade_by_symbol.join(side_turnover, how="left")

    order_regular = order_events.loc[
        (order_events.Time.ge(93_000_000) & order_events.Time.lt(113_000_000))
        | (order_events.Time.ge(130_000_000) & order_events.Time.lt(145_700_000))
    ].copy()
    order_regular["bucket_seconds"] = (raw_to_millis(order_regular.Time) // 300_000 * 300).astype("int64")
    order_regular["signed_volume"] = order_regular.Volume * np.where(order_regular.FunctionCode.eq(BUY), 1, -1)
    adds_by_symbol = order_regular.loc[order_regular.OrderKind.eq(ADD)].groupby("Symbol").agg(
        add_count=("Volume", "size"), add_volume=("Volume", "sum"), add_signed_volume=("signed_volume", "sum")
    )
    cancels_by_symbol = order_regular.loc[order_regular.OrderKind.eq(DELETE)].groupby("Symbol").agg(
        cancel_count=("Volume", "size"), cancel_volume=("Volume", "sum"), cancel_signed_volume=("signed_volume", "sum")
    )
    per_stock = pd.DataFrame(index=pd.Index(symbols, name="Symbol"))
    for frame in [trade_by_symbol, adds_by_symbol, cancels_by_symbol]:
        per_stock = per_stock.join(frame, how="left")
    per_stock = per_stock.join(snap.groupby("Symbol").agg(
        snapshot_count=("Time", "size"), median_spread_bps=("spread_bps", "median"),
        mean_spread_bps=("spread_bps", "mean"), median_l5_depth_value=("depth_value5", "median"),
        median_l5_depth_imbalance=("depth_imbalance5", "median"),
    ), how="left").fillna(0)
    per_stock["cancel_add_count_ratio"] = per_stock.cancel_count / per_stock.add_count.replace(0, np.nan)
    per_stock["cancel_add_volume_ratio"] = per_stock.cancel_volume / per_stock.add_volume.replace(0, np.nan)
    per_stock["add_volume_imbalance"] = per_stock.add_signed_volume / per_stock.add_volume.replace(0, np.nan)
    per_stock["cancel_volume_imbalance"] = per_stock.cancel_signed_volume / per_stock.cancel_volume.replace(0, np.nan)
    per_stock = per_stock.replace([np.inf, -np.inf], np.nan).reset_index()

    directional["bucket_seconds"] = (directional.trade_millis // 300_000 * 300).astype("int64")
    stock_intraday = directional.groupby(["Symbol", "bucket_seconds"]).agg(
        trade_count=("Volume", "size"), volume=("Volume", "sum"), turnover=("amount", "sum"),
        signed_turnover=("signed_amount", "sum"), large_turnover=("amount", lambda values: values[directional.loc[values.index, "large_ticket"]].sum()),
    ).reset_index()
    stock_intraday["time"] = stock_intraday.bucket_seconds.map(time_label)
    amount_bands = [0, 10_000, 50_000, 100_000, 500_000, np.inf]
    band_names = ["<1万元", "1–5万元", "5–10万元", "10–50万元", "≥50万元"]
    directional["amount_band"] = pd.cut(
        directional.amount, bins=amount_bands, labels=band_names, right=False, include_lowest=True
    )
    total_directional_turnover = float(directional.amount.sum())
    total_directional_count = int(len(directional))
    size_bands = directional.groupby("amount_band", observed=False).agg(
        trade_count=("Volume", "size"),
        volume=("Volume", "sum"),
        turnover=("amount", "sum"),
        median_trade_amount=("amount", "median"),
    ).reindex(band_names).fillna(0).reset_index()
    size_bands["trade_share"] = size_bands.trade_count / max(1, total_directional_count)
    size_bands["turnover_share"] = size_bands.turnover / max(1.0, total_directional_turnover)

    threshold = directional.amount.ge(100_000)
    large_ticket = directional.loc[threshold]
    large_ticket_stats = {
        "threshold_yuan": 100_000,
        "trade_count": int(len(large_ticket)),
        "trade_share": safe_float(len(large_ticket) / max(1, len(directional))),
        "turnover": safe_float(large_ticket.amount.sum(), 2),
        "turnover_share": safe_float(large_ticket.amount.sum() / max(1, directional.amount.sum())),
    }

    active_by_side = directional.groupby("BSFlag").agg(
        trade_count=("Volume", "size"), volume=("Volume", "sum"), turnover=("amount", "sum")
    )
    active_buy = active_by_side.loc[BUY] if BUY in active_by_side.index else pd.Series({"trade_count": 0, "volume": 0, "turnover": 0})
    active_sell = active_by_side.loc[SELL] if SELL in active_by_side.index else pd.Series({"trade_count": 0, "volume": 0, "turnover": 0})
    active_total = float(active_buy.turnover + active_sell.turnover)

    directional["bucket_seconds"] = (directional.trade_seconds // 300 * 300).astype("int64")
    intraday = directional.groupby("bucket_seconds").agg(
        trade_count=("Volume", "size"), volume=("Volume", "sum"), turnover=("amount", "sum")
    ).reset_index().sort_values("bucket_seconds")
    intraday["time"] = intraday.bucket_seconds.map(time_label)
    intraday["seconds"] = intraday.bucket_seconds

    median_size = directional.amount.median()
    p90_size = directional.amount.quantile(0.90)
    p99_size = directional.amount.quantile(0.99)
    order_function_counts = orders.FunctionCode.value_counts().sort_index()
    order_kind_counts = orders.OrderKind.value_counts().sort_index()
    order_symbol_channels = orders.groupby("Symbol").Channel.nunique()
    trade_regular_channels = sorted(int(value) for value in trades.loc[trades.Time.le(150_000_000), "Channel"].unique())
    regular_channels = sorted(int(value) for value in set(orders.Channel.unique()) & set(trades.loc[trades.Time.le(150_000_000), "Channel"].unique()))

    snapshot_delta = snap.sort_values(["Symbol", "Time"]).groupby("Symbol").Time.apply(lambda s: raw_to_millis(s).diff())
    snapshot_gap_median_ms = safe_float(snapshot_delta.dropna().median(), 1)
    snapshot_gap_p90_ms = safe_float(snapshot_delta.dropna().quantile(0.90), 1)

    directional_turnover = float(directional.amount.sum())
    continuous_trade_amount = float(continuous.amount.sum())
    order_cancel_count_ratio = len(order_cancels) / max(1, len(order_adds))
    order_cancel_volume_ratio = order_cancels.Volume.sum() / max(1, order_adds.Volume.sum())

    strategy_screen = build_strategy_screen(directional, order_regular, snap)
    return {
        "date": DATE,
        "scope": {
            "symbols": len(symbols),
            "symbol_codes": symbols,
            "orders_rows": int(len(orders)),
            "trade_rows_all": int(len(trades)),
            "snapshot_rows": int(len(snapshots)),
            "regular_trade_count": int(len(continuous)),
            "directional_trade_count": int(len(directional)),
            "regular_trade_volume": int(continuous.Volume.sum()),
            "regular_trade_turnover": safe_float(continuous_trade_amount, 2),
            "auction_trade_count": int(len(auction)),
            "auction_trade_turnover": safe_float(auction.amount.sum(), 2),
            "post_close_trade_count": int(len(after)),
            "post_close_trade_turnover": safe_float(after.amount.sum(), 2),
            "active_buy_turnover": safe_float(active_buy.turnover, 2),
            "active_sell_turnover": safe_float(active_sell.turnover, 2),
            "active_buy_share": safe_float(active_buy.turnover / max(1, active_total)),
            "active_sell_share": safe_float(active_sell.turnover / max(1, active_total)),
            "active_flow_imbalance": safe_float((active_buy.turnover - active_sell.turnover) / max(1, active_total)),
            "order_add_count": int(len(order_adds)),
            "order_cancel_count": int(len(order_cancels)),
            "order_add_volume": int(order_adds.Volume.sum()),
            "order_cancel_volume": int(order_cancels.Volume.sum()),
            "observed_cancel_add_count_ratio": safe_float(order_cancel_count_ratio),
            "observed_cancel_add_volume_ratio": safe_float(order_cancel_volume_ratio),
        },
        "trade_size": {
            "amount_quantiles": {
                "p50": safe_float(directional.amount.quantile(.50), 2),
                "p75": safe_float(directional.amount.quantile(.75), 2),
                "p90": safe_float(p90_size, 2),
                "p95": safe_float(directional.amount.quantile(.95), 2),
                "p99": safe_float(p99_size, 2),
            },
            "median_amount": safe_float(median_size, 2),
            "p90_amount": safe_float(p90_size, 2),
            "p99_amount": safe_float(p99_size, 2),
            "ge_100k": large_ticket_stats,
            "bands": json_records(size_bands),
            "scope_note": "只统计连续竞价中 BSFlag=B/S 的成交；集合竞价与盘后定价成交另列，避免其聚合成交影响单笔大小分布。",
        },
        "order_events": {
            "function_code_counts": {str(int(key)): int(value) for key, value in order_function_counts.items()},
            "order_kind_counts": {str(int(key)): int(value) for key, value in order_kind_counts.items()},
            "regular_channel_ids": regular_channels,
            "all_trade_channel_ids_through_1500": trade_regular_channels,
            "max_channels_per_symbol_in_ord": int(order_symbol_channels.max()),
            "note": "FunctionCode 66/83 与 OrderKind 65/68 是 B/S 和 A/D 的 ASCII 数值；其余代码应按状态/标记事件单独解释。BizIndex 按 Channel 连续，全市场其他证券未包含时，样本内序号有跳号不等于丢包。",
        },
        "snapshots": {
            "levels_per_side": 10,
            "continuous_rows_used": int(len(snap)),
            "median_update_gap_ms": snapshot_gap_median_ms,
            "p90_update_gap_ms": snapshot_gap_p90_ms,
            "median_spread_bps": safe_float(snap.spread_bps.median(), 4),
            "median_depth_imbalance5": safe_float(snap.depth_imbalance5.median(), 4),
            "median_depth_value5": safe_float(snap.depth_value5.median(), 2),
            "note": "盘口统计来自连续竞价快照；快照是周期性/变动触发，不是逐笔委托事件。这里按快照记录计算描述统计，不等同于严格时间加权均值。",
        },
        "intraday_5m": json_records(intraday[["time", "seconds", "trade_count", "volume", "turnover"]]),
        "stocks": json_records(per_stock.round(4)),
        "stock_intraday_5m": json_records(stock_intraday),
        "strategy_screen": strategy_screen,
        "insights": {
            "large_ticket_turnover_share_ge_100k": large_ticket_stats["turnover_share"],
            "large_ticket_count_share_ge_100k": large_ticket_stats["trade_share"],
            "active_flow_imbalance": safe_float((active_buy.turnover - active_sell.turnover) / max(1, active_total)),
            "size_definition": "成交金额 = 成交价 × 成交股数；≥10万元只是本次探索性阈值，不是交易所或监管定义的大单标准。",
        },
        "interpretation_limits": [
            "单个交易日的横截面只能提出待检验假设，不能证明稳定规律或因果关系。",
            "主动买卖额差是基于 BSFlag 的订单流压力代理，不是净资金流；每笔成交必有买卖双方。",
            "新增/撤单量比是当前消息样本中的观察比率，不是全市场真实委托取消概率。",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, default=ROOT / "ord_20260923.parquet")
    parser.add_argument("--trades", type=Path, default=ROOT / "exe_20260923.parquet")
    parser.add_argument("--snapshots", type=Path, default=ROOT / "snp_20260923.parquet")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for path in [args.orders, args.trades, args.snapshots]:
        if not path.exists():
            raise FileNotFoundError(path)
    report = build_report(args.orders, args.trades, args.snapshots)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    scope = report["scope"]
    large = report["trade_size"]["ge_100k"]
    print(f"Wrote {args.output}")
    print(f"Securities={scope['symbols']}; continuous trades={scope['directional_trade_count']:,}; turnover=¥{scope['regular_trade_turnover']:,.0f}")
    print(f"Trades >=¥100k: {large['trade_count']:,} ({large['trade_share']:.2%} of prints), {large['turnover_share']:.2%} of turnover")
    print(f"Median snapshot spread={report['snapshots']['median_spread_bps']:.2f} bp")


if __name__ == "__main__":
    main()
