#!/usr/bin/env python3
"""Replay SSE L2 order/trade events and emit trade tape plus one-second L5.

This is an event replay, not a synthetic matching engine driven by orders alone:
the SSE feed omits some fully executed aggressive orders from ``ord``.  The
``exe`` stream is therefore the authoritative match event stream; its passive
orders are resolved against the reconstructed book and all resolvable orders
are decremented at each match.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


PRICE_SCALE = 10_000
BUY = 66  # ASCII B
SELL = 83  # ASCII S
ADD = 65  # ASCII A
DELETE = 68  # ASCII D
TRADE_BUY = 66
TRADE_SELL = 83
TRADE_AUCTION = 0

ORDER_COLUMNS = [
    "BizIndex", "FunctionCode", "OrderKind", "OrderOriNo", "Price", "Time", "Volume", "Symbol"
]
TRADE_COLUMNS = [
    "AskOrder", "BSFlag", "BidOrder", "BizIndex", "Channel", "FunctionCode", "Index",
    "OrderKind", "Price", "Time", "Volume", "Symbol"
]
BOOK_COLUMNS = [
    "Time", "Symbol", "BSFlag", "AccVolume", "TotalAskVolume", "TotalBidVolume",
    *[f"AskPrice{i}" for i in range(1, 6)], *[f"AskVolume{i}" for i in range(1, 6)],
    *[f"BidPrice{i}" for i in range(1, 6)], *[f"BidVolume{i}" for i in range(1, 6)],
]


def price_to_ticks(price: Any) -> int:
    """Convert float32 Yuan prices to an exact integer at 1/10000 Yuan."""
    if not np.isfinite(price):
        return 0
    return int(round(float(price) * PRICE_SCALE))


def ticks_to_price(ticks: int) -> float:
    return ticks / PRICE_SCALE


def raw_time_from_seconds(seconds: int) -> int:
    hour, rem = divmod(seconds, 3600)
    minute, second = divmod(rem, 60)
    return hour * 10_000_000 + minute * 100_000 + second * 1_000


def seconds_from_raw_time(raw_time: int) -> int:
    hour = raw_time // 10_000_000
    minute = (raw_time // 100_000) % 100
    second = (raw_time // 1_000) % 100
    return hour * 3600 + minute * 60 + second


def session_name(raw_time: int) -> str:
    if 91_500_000 <= raw_time <= 92_500_000:
        return "opening_auction"
    if 93_000_000 <= raw_time <= 113_000_000 or 130_000_000 <= raw_time < 145_700_000:
        return "continuous"
    if 145_700_000 <= raw_time <= 150_000_000:
        return "closing_auction"
    if 150_000_000 < raw_time <= 153_000_000:
        return "after_hours_fixed_price"
    return "other"


def is_continuous_snapshot_time(raw_time: int) -> bool:
    return 93_000_000 <= raw_time <= 113_000_000 or 130_000_000 <= raw_time <= 150_000_000


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OrderBook:
    """Price aggregated book plus an order-id index, with lazy price heaps."""

    def __init__(self) -> None:
        self.orders: dict[int, list[int]] = {}  # order id -> [side, price ticks, remaining qty]
        self.levels: dict[int, dict[int, int]] = {1: {}, -1: {}}
        self.heaps: dict[int, list[tuple[int, int, int]]] = {1: [], -1: []}
        self.versions: dict[int, dict[int, int]] = {1: defaultdict(int), -1: defaultdict(int)}

    def _adjust_level(self, side: int, price: int, delta: int) -> None:
        current = self.levels[side].get(price, 0)
        updated = current + delta
        if updated <= 0:
            self.levels[side].pop(price, None)
            if current > 0:
                self.versions[side][price] += 1
            return
        self.levels[side][price] = updated
        if current == 0:
            self.versions[side][price] += 1
            version = self.versions[side][price]
            heap_key = -price if side == 1 else price
            heapq.heappush(self.heaps[side], (heap_key, price, version))

    def add(self, order_id: int, side: int, price: int, quantity: int) -> bool:
        if quantity <= 0 or price <= 0 or order_id in self.orders:
            return False
        self.orders[order_id] = [side, price, quantity]
        self._adjust_level(side, price, quantity)
        return True

    def reduce(self, order_id: int, quantity: int) -> tuple[str, int | None]:
        order = self.orders.get(order_id)
        if order is None:
            return "missing", None
        side, price, remaining = order
        if quantity <= 0 or quantity > remaining:
            return "overdraw", remaining
        remaining -= quantity
        self._adjust_level(side, price, -quantity)
        if remaining == 0:
            del self.orders[order_id]
        else:
            order[2] = remaining
        return "ok", remaining

    def best_price(self, side: int) -> int | None:
        heap = self.heaps[side]
        while heap:
            _, price, version = heap[0]
            if self.versions[side].get(price, 0) != version or self.levels[side].get(price, 0) <= 0:
                heapq.heappop(heap)
            else:
                return price
        return None

    def top_levels(self, side: int, count: int = 5) -> list[tuple[int, int]]:
        heap = self.heaps[side]
        result: list[tuple[int, int, int]] = []
        levels: list[tuple[int, int]] = []
        while len(levels) < count:
            while heap:
                _, price, version = heap[0]
                if self.versions[side].get(price, 0) != version or self.levels[side].get(price, 0) <= 0:
                    heapq.heappop(heap)
                else:
                    break
            if not heap:
                break
            item = heapq.heappop(heap)
            result.append(item)
            levels.append((item[1], self.levels[side][item[1]]))
        for item in result:
            heapq.heappush(heap, item)
        return levels

    def total(self, side: int) -> int:
        return sum(self.levels[side].values())


def sample_grid() -> list[int]:
    periods = [(9 * 3600 + 15 * 60, 11 * 3600 + 30 * 60), (13 * 3600, 15 * 3600)]
    seconds = [second for start, end in periods for second in range(start, end + 1)]
    return [raw_time_from_seconds(value) for value in seconds]


def make_l5_row(symbol: int, raw_time: int, book: OrderBook) -> tuple[Any, ...]:
    asks = book.top_levels(-1, 5)
    bids = book.top_levels(1, 5)
    pad = lambda levels: levels + [(0, 0)] * (5 - len(levels))
    asks, bids = pad(asks), pad(bids)
    row: list[Any] = [raw_time, symbol, session_name(raw_time)]
    row.extend(ticks_to_price(price) for price, _ in bids)
    row.extend(quantity for _, quantity in bids)
    row.extend(ticks_to_price(price) for price, _ in asks)
    row.extend(quantity for _, quantity in asks)
    return tuple(row)


def compare_snapshot(book: OrderBook, ref: dict[str, Any], stats: dict[str, Any], symbol: int) -> None:
    raw_time = int(ref["Time"])
    if not is_continuous_snapshot_time(raw_time):
        return
    matches = True
    for side, price_prefix, volume_prefix in [(-1, "AskPrice", "AskVolume"), (1, "BidPrice", "BidVolume")]:
        actual = book.top_levels(side, 5)
        actual += [(0, 0)] * (5 - len(actual))
        for index, (price, quantity) in enumerate(actual, start=1):
            ref_price = price_to_ticks(ref[f"{price_prefix}{index}"])
            ref_volume = int(round(float(ref[f"{volume_prefix}{index}"])))
            equal = price == ref_price and quantity == ref_volume
            stats["l5_levels_compared"] += 1
            if equal:
                stats["l5_levels_exact"] += 1
            else:
                matches = False
                if len(stats["snapshot_mismatch_examples"]) < 30:
                    stats["snapshot_mismatch_examples"].append({
                        "Symbol": symbol,
                        "Time": raw_time,
                        "side": "ask" if side == -1 else "bid",
                        "level": index,
                        "reference_price": ticks_to_price(ref_price),
                        "reference_volume": ref_volume,
                        "replay_price": ticks_to_price(price),
                        "replay_volume": quantity,
                    })
    stats["snapshot_rows_compared"] += 1
    if matches:
        stats["snapshot_rows_exact"] += 1

    expected_acc_volume = int(ref["AccVolume"])
    if stats["replay_cum_volume"] == expected_acc_volume:
        stats["acc_volume_exact"] += 1
    else:
        stats["acc_volume_mismatches"] += 1
        if len(stats["acc_volume_mismatch_examples"]) < 20:
            stats["acc_volume_mismatch_examples"].append({
                "Symbol": symbol,
                "Time": raw_time,
                "reference": expected_acc_volume,
                "replay": stats["replay_cum_volume"],
            })

    if "TotalBidVolume" in ref and "TotalAskVolume" in ref:
        bid_match = book.total(1) == int(ref["TotalBidVolume"])
        ask_match = book.total(-1) == int(ref["TotalAskVolume"])
        stats["total_side_volumes_compared"] += 2
        stats["total_side_volumes_exact"] += int(bid_match) + int(ask_match)


def replay_symbol(
    symbol: int,
    orders_for_symbol: pd.DataFrame,
    trades_for_symbol: pd.DataFrame,
    snapshots_for_symbol: pd.DataFrame,
    l5_writer: pq.ParquetWriter | None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "symbol": symbol,
        "order_adds": 0,
        "order_cancels": 0,
        "trades_replayed": 0,
        "trade_volume_replayed": 0,
        "ghost_aggressor_references": 0,
        "missing_passive_references": 0,
        "missing_auction_references": 0,
        "cancel_missing_order": 0,
        "cancel_overdraw": 0,
        "trade_overdraw": 0,
        "duplicate_live_order_id": 0,
        "trade_price_vs_passive_mismatch": 0,
        "trade_price_priority_violations": 0,
        "event_time_order_violations": 0,
        "snapshot_rows_compared": 0,
        "snapshot_rows_exact": 0,
        "l5_levels_compared": 0,
        "l5_levels_exact": 0,
        "acc_volume_exact": 0,
        "acc_volume_mismatches": 0,
        "total_side_volumes_compared": 0,
        "total_side_volumes_exact": 0,
        "snapshot_mismatch_examples": [],
        "acc_volume_mismatch_examples": [],
        "trade_validation_examples": [],
        "replay_cum_volume": 0,
    }

    order_rows = orders_for_symbol[
        (orders_for_symbol["Time"] <= 150_000_000)
        & orders_for_symbol["FunctionCode"].isin([BUY, SELL])
        & orders_for_symbol["OrderKind"].isin([ADD, DELETE])
    ].sort_values("BizIndex", kind="stable")
    trade_rows = trades_for_symbol[trades_for_symbol["Time"] <= 150_000_000].sort_values("BizIndex", kind="stable")

    # This is the exchange ordering key. Timestamps only break ties if BizIndex collides.
    order_iter = iter(order_rows[["BizIndex", "FunctionCode", "OrderKind", "OrderOriNo", "Price", "Time", "Volume"]].itertuples(index=False, name=None))
    trade_iter = iter(trade_rows[["BizIndex", "BidOrder", "AskOrder", "BSFlag", "Price", "Time", "Volume"]].itertuples(index=False, name=None))
    order_event = next(order_iter, None)
    trade_event = next(trade_iter, None)

    ref_rows = snapshots_for_symbol[
        snapshots_for_symbol["Time"].map(is_continuous_snapshot_time)
    ].sort_values("Time", kind="stable")
    ref_by_time = {int(row["Time"]): row for row in ref_rows.to_dict("records")}

    grid = sample_grid()
    grid_index = 0
    book = OrderBook()
    previous_event_time: int | None = None
    seen_order_ids: set[int] = set()
    l5_rows: list[tuple[Any, ...]] = []

    def emit_until(raw_time: int) -> None:
        nonlocal grid_index
        while grid_index < len(grid) and grid[grid_index] < raw_time:
            sample_time = grid[grid_index]
            l5_rows.append(make_l5_row(symbol, sample_time, book))
            reference = ref_by_time.get(sample_time)
            if reference is not None:
                compare_snapshot(book, reference, stats, symbol)
            grid_index += 1

    def record_trade_error(kind: str, raw_time: int, order_id: int, extra: dict[str, Any] | None = None) -> None:
        if len(stats["trade_validation_examples"]) < 30:
            item = {"kind": kind, "Symbol": symbol, "Time": raw_time, "order_id": order_id}
            if extra:
                item.update(extra)
            stats["trade_validation_examples"].append(item)

    def consume(order_id: int, quantity: int, raw_time: int, *, passive: bool, auction: bool = False) -> None:
        result, remaining = book.reduce(order_id, quantity)
        if result == "missing":
            if auction:
                stats["missing_auction_references"] += 1
            elif passive:
                stats["missing_passive_references"] += 1
                record_trade_error("missing_passive_order", raw_time, order_id)
            else:
                stats["ghost_aggressor_references"] += 1
            return
        if result == "overdraw":
            stats["trade_overdraw"] += 1
            record_trade_error("trade_exceeds_remaining", raw_time, order_id, {"trade_volume": quantity, "remaining": remaining})
            if remaining and remaining > 0:
                book.reduce(order_id, remaining)

    while order_event is not None or trade_event is not None:
        take_order = trade_event is None or (
            order_event is not None and int(order_event[0]) <= int(trade_event[0])
        )
        event_time = int(order_event[5] if take_order else trade_event[5])
        emit_until(event_time)

        if previous_event_time is not None and event_time < previous_event_time:
            stats["event_time_order_violations"] += 1
        previous_event_time = event_time

        if take_order:
            _, function_code, order_kind, order_id_raw, raw_price, _, quantity_raw = order_event
            order_id = int(order_id_raw)
            quantity = int(quantity_raw)
            side = 1 if int(function_code) == BUY else -1
            if int(order_kind) == ADD:
                price = price_to_ticks(raw_price)
                if order_id in seen_order_ids:
                    stats["duplicate_live_order_id"] += 1
                    record_trade_error("duplicate_order_id", event_time, order_id)
                seen_order_ids.add(order_id)
                if not book.add(order_id, side, price, quantity):
                    stats["duplicate_live_order_id"] += 1
                    record_trade_error("invalid_add", event_time, order_id, {"price": price, "volume": quantity})
                else:
                    stats["order_adds"] += 1
            else:
                stats["order_cancels"] += 1
                current = book.orders.get(order_id)
                if current is None:
                    stats["cancel_missing_order"] += 1
                    record_trade_error("cancel_missing_order", event_time, order_id)
                elif current[0] != side:
                    stats["cancel_missing_order"] += 1
                    record_trade_error("cancel_side_mismatch", event_time, order_id)
                else:
                    result, remaining = book.reduce(order_id, quantity)
                    if result != "ok":
                        stats["cancel_overdraw"] += 1
                        record_trade_error("cancel_exceeds_remaining", event_time, order_id, {"cancel_volume": quantity, "remaining": remaining})
                        if remaining and remaining > 0:
                            book.reduce(order_id, remaining)
            order_event = next(order_iter, None)
        else:
            _, bid_id_raw, ask_id_raw, bs_flag_raw, raw_price, _, quantity_raw = trade_event
            bid_id, ask_id = int(bid_id_raw), int(ask_id_raw)
            bs_flag = int(bs_flag_raw)
            trade_price = price_to_ticks(raw_price)
            quantity = int(quantity_raw)
            stats["trades_replayed"] += 1
            stats["trade_volume_replayed"] += quantity
            stats["replay_cum_volume"] += quantity

            if bs_flag == TRADE_BUY:
                passive_id, passive_side = ask_id, -1
                passive = book.orders.get(passive_id)
                if passive is not None:
                    if passive[0] != passive_side:
                        stats["missing_passive_references"] += 1
                        record_trade_error("passive_side_mismatch", event_time, passive_id)
                    else:
                        if passive[1] != trade_price:
                            stats["trade_price_vs_passive_mismatch"] += 1
                            record_trade_error("passive_price_mismatch", event_time, passive_id, {"trade_price": ticks_to_price(trade_price), "order_price": ticks_to_price(passive[1])})
                        best = book.best_price(passive_side)
                        if best is not None and best != passive[1]:
                            stats["trade_price_priority_violations"] += 1
                            record_trade_error("ask_price_priority", event_time, passive_id, {"best_ask": ticks_to_price(best), "passive_price": ticks_to_price(passive[1])})
                consume(passive_id, quantity, event_time, passive=True)
                consume(bid_id, quantity, event_time, passive=False)
            elif bs_flag == TRADE_SELL:
                passive_id, passive_side = bid_id, 1
                passive = book.orders.get(passive_id)
                if passive is not None:
                    if passive[0] != passive_side:
                        stats["missing_passive_references"] += 1
                        record_trade_error("passive_side_mismatch", event_time, passive_id)
                    else:
                        if passive[1] != trade_price:
                            stats["trade_price_vs_passive_mismatch"] += 1
                            record_trade_error("passive_price_mismatch", event_time, passive_id, {"trade_price": ticks_to_price(trade_price), "order_price": ticks_to_price(passive[1])})
                        best = book.best_price(passive_side)
                        if best is not None and best != passive[1]:
                            stats["trade_price_priority_violations"] += 1
                            record_trade_error("bid_price_priority", event_time, passive_id, {"best_bid": ticks_to_price(best), "passive_price": ticks_to_price(passive[1])})
                consume(passive_id, quantity, event_time, passive=True)
                consume(ask_id, quantity, event_time, passive=False)
            elif bs_flag == TRADE_AUCTION:
                consume(bid_id, quantity, event_time, passive=True, auction=True)
                consume(ask_id, quantity, event_time, passive=True, auction=True)
            else:
                stats["missing_passive_references"] += 1
                record_trade_error("unknown_trade_bs_flag", event_time, bid_id, {"BSFlag": bs_flag})
            trade_event = next(trade_iter, None)

    while grid_index < len(grid):
        sample_time = grid[grid_index]
        l5_rows.append(make_l5_row(symbol, sample_time, book))
        reference = ref_by_time.get(sample_time)
        if reference is not None:
            compare_snapshot(book, reference, stats, symbol)
        grid_index += 1

    columns = [
        "Time", "Symbol", "Session",
        *[f"BidPrice{i}" for i in range(1, 6)], *[f"BidVolume{i}" for i in range(1, 6)],
        *[f"AskPrice{i}" for i in range(1, 6)], *[f"AskVolume{i}" for i in range(1, 6)],
    ]
    frame = pd.DataFrame.from_records(l5_rows, columns=columns)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if l5_writer is None:
        raise RuntimeError("Parquet writer must be initialized before replay")
    l5_writer.write_table(table, row_group_size=65_536)
    stats["l5_rows_written"] = len(frame)
    stats["live_orders_at_150000000"] = len(book.orders)
    stats["book_bid_volume_at_150000000"] = book.total(1)
    stats["book_ask_volume_at_150000000"] = book.total(-1)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", default="ord_20260923.parquet")
    parser.add_argument("--trades", default="exe_20260923.parquet")
    parser.add_argument("--snapshots", default="snp_20260923.parquet")
    parser.add_argument("--output-dir", default="exchange_simulation_output")
    parser.add_argument(
        "--expected-package-sha256",
        default="18c0e124a9c68eb2f77b965ca1f29c93bb30339d7db45245e1af3c0420c6c947",
    )
    args = parser.parse_args()

    order_path, trade_path, snapshot_path = map(Path, [args.orders, args.trades, args.snapshots])
    for path in [order_path, trade_path, snapshot_path]:
        if not path.exists():
            raise FileNotFoundError(path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Reading Parquet inputs...", flush=True)
    orders = pd.read_parquet(order_path, columns=ORDER_COLUMNS)
    trades = pd.read_parquet(trade_path, columns=TRADE_COLUMNS)
    snapshots = pd.read_parquet(snapshot_path, columns=BOOK_COLUMNS)
    symbols = sorted(set(orders["Symbol"].unique()) | set(trades["Symbol"].unique()) | set(snapshots["Symbol"].unique()))

    input_hashes = {path.name: file_sha256(path) for path in [order_path, trade_path, snapshot_path]}
    order_add_count = int(((orders.FunctionCode.isin([BUY, SELL])) & (orders.OrderKind == ADD)).sum())
    order_cancel_count = int(((orders.FunctionCode.isin([BUY, SELL])) & (orders.OrderKind == DELETE)).sum())
    marker_counts = {
        f"function_{int(k)}_kind_{int(kind)}": int(count)
        for (k, kind), count in orders.groupby(["FunctionCode", "OrderKind"]).size().items()
        if not (int(k) in (BUY, SELL) and int(kind) in (ADD, DELETE))
    }

    l5_path = output_dir / "l5_1s_20260923.parquet"
    trade_out_path = output_dir / "trades_replayed_20260923.parquet"
    report_path = output_dir / "validation_report_20260923.json"
    writer: pq.ParquetWriter | None = None
    per_symbol: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    try:
        for index, symbol in enumerate(symbols, start=1):
            print(f"[{index}/{len(symbols)}] Replaying {symbol}...", flush=True)
            result = replay_symbol(
                int(symbol),
                orders.loc[orders.Symbol == symbol],
                trades.loc[trades.Symbol == symbol],
                snapshots.loc[snapshots.Symbol == symbol],
                writer,
            ) if writer is not None else None

            # Initialize a writer from the first symbol's output schema, then replay it.
            # The first pass above is avoided by opening a schema-compatible writer below.
            if result is None:
                schema = pa.schema([
                    ("Time", pa.int64()), ("Symbol", pa.int64()), ("Session", pa.string()),
                    *[(f"BidPrice{i}", pa.float64()) for i in range(1, 6)],
                    *[(f"BidVolume{i}", pa.int64()) for i in range(1, 6)],
                    *[(f"AskPrice{i}", pa.float64()) for i in range(1, 6)],
                    *[(f"AskVolume{i}", pa.int64()) for i in range(1, 6)],
                ])
                writer = pq.ParquetWriter(l5_path, schema, compression="snappy")
                result = replay_symbol(
                    int(symbol),
                    orders.loc[orders.Symbol == symbol],
                    trades.loc[trades.Symbol == symbol],
                    snapshots.loc[snapshots.Symbol == symbol],
                    writer,
                )
            per_symbol.append(result)
            for key, value in result.items():
                if isinstance(value, int) and key != "symbol":
                    totals[key] += value
            print(
                f"  trades={result['trades_replayed']:,}, "
                f"snapshot exact={result['snapshot_rows_exact']}/{result['snapshot_rows_compared']}, "
                f"passive-missing={result['missing_passive_references']}, "
                f"cancel-errors={result['cancel_missing_order'] + result['cancel_overdraw']}",
                flush=True,
            )
    finally:
        if writer is not None:
            writer.close()

    # Keep the whole execution feed in the trade tape, including the 15:05-15:30
    # fixed-price session. The book replay above ends at 15:00 because the order
    # file has no post-close order events.
    trade_output = trades.copy()
    trade_output["PriceTicks"] = np.rint(trade_output["Price"].astype("float64") * PRICE_SCALE).astype("int64")
    trade_output["AggressorSide"] = trade_output["BSFlag"].map({66: "BUY", 83: "SELL", 0: "AUCTION"}).fillna("UNKNOWN")
    trade_output["Session"] = trade_output["Time"].map(session_name)
    trade_output.to_parquet(trade_out_path, index=False, compression="snappy")

    regular_trades = trades[trades.Time <= 150_000_000]
    post_close_trades = trades[trades.Time > 150_000_000]
    report = {
        "date": "2026-09-23",
        "inputs": {
            "files": input_hashes,
            "rows": {
                "orders": int(len(orders)),
                "trades": int(len(trades)),
                "snapshots": int(len(snapshots)),
            },
            "symbols": int(len(symbols)),
            "order_adds": order_add_count,
            "order_cancels": order_cancel_count,
            "non_order_marker_counts": marker_counts,
            "provided_package_sha256": args.expected_package_sha256,
            "package_archive_sha256_verified": False,
            "package_hash_note": "No package archive is present in the workspace; individual Parquet SHA-256 values are recorded above and cannot be compared directly with a package hash.",
        },
        "outputs": {
            "trades": str(trade_out_path),
            "l5_1s": str(l5_path),
            "validation_report": str(report_path),
            "trade_rows_written": int(len(trade_output)),
            "trade_volume_written": int(trade_output.Volume.sum()),
            "regular_session_trade_rows_replayed": int(len(regular_trades)),
            "regular_session_trade_volume_replayed": int(regular_trades.Volume.sum()),
            "post_close_trade_rows_retained_in_trade_tape": int(len(post_close_trades)),
            "post_close_trade_volume_retained_in_trade_tape": int(post_close_trades.Volume.sum()),
            "l5_sampling": "Every second from 09:15:00 through 11:30:00 and 13:00:00 through 15:00:00; no lunch rows.",
        },
        "validation_totals": {key: int(value) for key, value in totals.items()},
        "snapshot_l5_exact_rate": (
            float(totals["l5_levels_exact"] / totals["l5_levels_compared"])
            if totals["l5_levels_compared"] else None
        ),
        "snapshot_exact_row_rate": (
            float(totals["snapshot_rows_exact"] / totals["snapshot_rows_compared"])
            if totals["snapshot_rows_compared"] else None
        ),
        "acc_volume_exact_rate": (
            float(totals["acc_volume_exact"] / (totals["acc_volume_exact"] + totals["acc_volume_mismatches"]))
            if totals["acc_volume_exact"] + totals["acc_volume_mismatches"] else None
        ),
        "total_side_volume_exact_rate": (
            float(totals["total_side_volumes_exact"] / totals["total_side_volumes_compared"])
            if totals["total_side_volumes_compared"] else None
        ),
        "known_scope_limit": "Post-close fixed-price executions (15:05-15:30) are retained in the trade tape. They are not applied to L5 because the order file contains no corresponding post-close add/cancel events.",
        "per_symbol": per_symbol,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nValidation summary", flush=True)
    for key in [
        "trades_replayed", "trade_volume_replayed", "ghost_aggressor_references",
        "missing_passive_references", "cancel_missing_order", "cancel_overdraw", "trade_overdraw",
        "trade_price_vs_passive_mismatch", "trade_price_priority_violations",
        "snapshot_rows_exact", "snapshot_rows_compared", "l5_levels_exact", "l5_levels_compared",
        "acc_volume_exact", "acc_volume_mismatches", "total_side_volumes_exact", "total_side_volumes_compared",
    ]:
        print(f"{key}: {totals[key]:,}")
    print(f"L5 level exact rate: {report['snapshot_l5_exact_rate']:.4%}")
    print(f"L5 row exact rate: {report['snapshot_exact_row_rate']:.4%}")
    print(f"Outputs: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
