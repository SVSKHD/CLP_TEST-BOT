import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from config.settings import BACKTEST_SPREAD_PIPS, BACKTEST_SLIPPAGE_PIPS, BACKTEST_COMMISSION_USD

logger = logging.getLogger("astra.simulator")

PIP_SIZE = 0.1
TRAIL_TRIGGER_PIPS = 9999   # effectively disabled — let SL/TP resolve naturally
TRAIL_DISTANCE_PIPS = 40


@dataclass
class TradeResult:
    symbol: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    lot: float
    pips: float
    pnl_usd: float
    result: str
    exit_reason: str


class Simulator:
    def __init__(self, spread_pips: float = None, slippage_pips: float = None,
                 commission_usd: float = None):
        self.spread_pips = spread_pips if spread_pips is not None else BACKTEST_SPREAD_PIPS
        self.slippage_pips = slippage_pips if slippage_pips is not None else BACKTEST_SLIPPAGE_PIPS
        self.commission_usd = commission_usd if commission_usd is not None else BACKTEST_COMMISSION_USD

    def execute_trade(self, df, entry_idx: int, direction: str, lot: float,
                      sl_price: float, tp_price: float, symbol: str,
                      pip_value_per_lot: float = 100.0) -> TradeResult:
        entry_candle = df.iloc[entry_idx]
        entry_time = entry_candle["time"]

        spread_offset = self.spread_pips * PIP_SIZE
        slip_offset = self.slippage_pips * PIP_SIZE

        if direction == "BUY":
            entry_price = entry_candle["close"] + spread_offset + slip_offset
        else:
            entry_price = entry_candle["close"] - spread_offset - slip_offset

        current_sl = sl_price
        trail_active = False

        for i in range(entry_idx + 1, len(df)):
            candle = df.iloc[i]

            if direction == "BUY":
                if candle["low"] <= current_sl:
                    exit_price = current_sl
                    pips = (exit_price - entry_price) / PIP_SIZE
                    reason = "TRAILING_SL" if trail_active else "SL_HIT"
                    result = "TRAILING_EXIT" if trail_active else "LOSS"
                    return self._build_result(
                        symbol, direction, entry_time, candle["time"],
                        entry_price, exit_price, sl_price, tp_price,
                        lot, pips, pip_value_per_lot, result, reason
                    )

                if candle["high"] >= tp_price:
                    exit_price = tp_price
                    pips = (exit_price - entry_price) / PIP_SIZE
                    return self._build_result(
                        symbol, direction, entry_time, candle["time"],
                        entry_price, exit_price, sl_price, tp_price,
                        lot, pips, pip_value_per_lot, "WIN", "TP_HIT"
                    )

                profit_pips = (candle["close"] - entry_price) / PIP_SIZE
                if profit_pips >= TRAIL_TRIGGER_PIPS:
                    new_sl = candle["close"] - TRAIL_DISTANCE_PIPS * PIP_SIZE
                    if new_sl > current_sl:
                        current_sl = new_sl
                        trail_active = True

            else:
                if candle["high"] >= current_sl:
                    exit_price = current_sl
                    pips = (entry_price - exit_price) / PIP_SIZE
                    reason = "TRAILING_SL" if trail_active else "SL_HIT"
                    result = "TRAILING_EXIT" if trail_active else "LOSS"
                    return self._build_result(
                        symbol, direction, entry_time, candle["time"],
                        entry_price, exit_price, sl_price, tp_price,
                        lot, pips, pip_value_per_lot, result, reason
                    )

                if candle["low"] <= tp_price:
                    exit_price = tp_price
                    pips = (entry_price - exit_price) / PIP_SIZE
                    return self._build_result(
                        symbol, direction, entry_time, candle["time"],
                        entry_price, exit_price, sl_price, tp_price,
                        lot, pips, pip_value_per_lot, "WIN", "TP_HIT"
                    )

                profit_pips = (entry_price - candle["close"]) / PIP_SIZE
                if profit_pips >= TRAIL_TRIGGER_PIPS:
                    new_sl = candle["close"] + TRAIL_DISTANCE_PIPS * PIP_SIZE
                    if new_sl < current_sl:
                        current_sl = new_sl
                        trail_active = True

        last_candle = df.iloc[-1]
        if direction == "BUY":
            exit_price = last_candle["close"]
            pips = (exit_price - entry_price) / PIP_SIZE
        else:
            exit_price = last_candle["close"]
            pips = (entry_price - exit_price) / PIP_SIZE

        return self._build_result(
            symbol, direction, entry_time, last_candle["time"],
            entry_price, exit_price, sl_price, tp_price,
            lot, pips, pip_value_per_lot, "EXPIRED", "END_OF_DATA"
        )

    def _build_result(self, symbol, direction, entry_time, exit_time,
                      entry_price, exit_price, sl_price, tp_price,
                      lot, pips, pip_value_per_lot, result, reason) -> TradeResult:
        pnl_usd = pips * lot * pip_value_per_lot - self.commission_usd * lot
        return TradeResult(
            symbol=symbol,
            direction=direction,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=round(entry_price, 2),
            exit_price=round(exit_price, 2),
            sl_price=round(sl_price, 2),
            tp_price=round(tp_price, 2),
            lot=lot,
            pips=round(pips, 1),
            pnl_usd=round(pnl_usd, 2),
            result=result,
            exit_reason=reason,
        )


if __name__ == "__main__":
    import pandas as pd
    import numpy as np

    np.random.seed(42)
    n = 100
    base = 2000.0
    prices = base + np.cumsum(np.random.randn(n) * 0.5)

    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
        "open": prices,
        "high": prices + np.random.rand(n) * 2,
        "low": prices - np.random.rand(n) * 2,
        "close": prices + np.random.randn(n) * 0.3,
    })

    sim = Simulator(spread_pips=2.5, slippage_pips=0.5, commission_usd=3.5)

    entry_price = df["close"].iloc[10]
    sl = entry_price - 40 * PIP_SIZE
    tp = entry_price + 60 * PIP_SIZE

    result = sim.execute_trade(df, 10, "BUY", 0.10, sl, tp, "XAUUSD")
    print(f"Trade result: {result.result} | PnL: ${result.pnl_usd:.2f} | "
          f"Pips: {result.pips:.1f} | {result.exit_reason}")
    print(f"Entry: {result.entry_price:.2f} -> Exit: {result.exit_price:.2f}")
