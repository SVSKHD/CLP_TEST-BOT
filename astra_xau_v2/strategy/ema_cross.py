import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np

from strategy.base import Signal
from core.market import calc_ema, calc_atr
from core.news_filter import is_news_blocked

logger = logging.getLogger("astra.ema_cross")

PIP_SIZE = 0.1
EMA_FAST = 8
EMA_SLOW = 21
EMA_HTF = 50
ATR_PERIOD = 14
ATR_MIN_PIPS = 18
RANGING_PIPS = 5
RANGING_LOOKBACK = 3
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.5
SL_MAX_PIPS = 80
SL_MIN_PIPS = 25
MIN_RR = 1.5
MAX_TRADES_PER_DAY = 3

LONDON_START = 7
LONDON_END = 12
NY_START = 13
NY_END = 18.5  # 18:30 GMT


class EMACrossStrategy:
    def __init__(self, symbol: str, mode: str = "live"):
        self.symbol = symbol
        self.mode = mode
        self._daily_trades = 0
        self._daily_pnl = 0.0
        self._current_date = None
        self._last_cross_direction = None  # track last confirmed cross for re-entry

    def generate_signal(self, df: pd.DataFrame, current_time: datetime = None) -> Optional[Signal]:
        if len(df) < 60:
            return None

        # Daily trade counter reset
        candle_time = df["time"].iloc[-1]
        candle_date = candle_time.date() if hasattr(candle_time, "date") else None
        if candle_date and candle_date != self._current_date:
            self._current_date = candle_date
            self._daily_trades = 0
            self._daily_pnl = 0.0

        # 3rd trade only allowed if daily PnL is positive
        if self._daily_trades >= MAX_TRADES_PER_DAY:
            return None
        if self._daily_trades == MAX_TRADES_PER_DAY - 1 and self._daily_pnl <= 0:
            return None

        # Session filter (skip in backtest — synthetic data has no TZ context)
        if self.mode != "backtest":
            hour = candle_time.hour if hasattr(candle_time, "hour") else 0
            minute = candle_time.minute if hasattr(candle_time, "minute") else 0
            time_frac = hour + minute / 60.0
            if not (LONDON_START <= time_frac < LONDON_END or NY_START <= time_frac < NY_END):
                return None

        # News filter
        if self.mode != "backtest":
            news = is_news_blocked(self.symbol)
            if news["blocked"]:
                return None

        # --- Indicators on M15 ---
        ema8 = calc_ema(df["close"], EMA_FAST)
        ema21 = calc_ema(df["close"], EMA_SLOW)
        atr = calc_atr(df, ATR_PERIOD)

        ema8_now = ema8.iloc[-1]
        ema8_prev = ema8.iloc[-2]
        ema21_now = ema21.iloc[-1]
        ema21_prev = ema21.iloc[-2]
        atr_val = atr.iloc[-1]
        atr_pips = atr_val / PIP_SIZE

        close = df["close"].iloc[-1]

        # ATR filter — market must be moving
        if atr_pips < ATR_MIN_PIPS:
            logger.debug(f"{self.symbol} SKIP: ATR {atr_pips:.0f}p < {ATR_MIN_PIPS}p (dead market)")
            return None

        # Ranging filter — EMA8 and EMA21 too close for last N candles
        recent_gap = abs(ema8.iloc[-RANGING_LOOKBACK:].values - ema21.iloc[-RANGING_LOOKBACK:].values)
        if all(g / PIP_SIZE < RANGING_PIPS for g in recent_gap):
            logger.debug(f"{self.symbol} SKIP: EMAs within {RANGING_PIPS}p for {RANGING_LOOKBACK} candles (ranging)")
            return None

        # Detect EMA cross
        cross_up = ema8_prev <= ema21_prev and ema8_now > ema21_now
        cross_down = ema8_prev >= ema21_prev and ema8_now < ema21_now

        signal_source = "cross"
        if cross_up:
            direction = "BUY"
            self._last_cross_direction = "BUY"
        elif cross_down:
            direction = "SELL"
            self._last_cross_direction = "SELL"
        elif self._last_cross_direction and self._check_pullback_reentry(
                df, ema8, ema21, self._last_cross_direction):
            direction = self._last_cross_direction
            signal_source = "pullback_reentry"
        else:
            return None

        # H1 trend filter
        h1 = self._resample_h1(df)
        if len(h1) < EMA_HTF + 1:
            return None
        h1_ema50 = calc_ema(h1["close"], EMA_HTF)
        h1_close = h1["close"].iloc[-1]
        h1_ema_val = h1_ema50.iloc[-1]

        if direction == "BUY" and h1_close <= h1_ema_val:
            logger.debug(f"{self.symbol} SKIP: BUY cross but H1 below EMA50 ({h1_close:.2f} < {h1_ema_val:.2f})")
            return None
        if direction == "SELL" and h1_close >= h1_ema_val:
            logger.debug(f"{self.symbol} SKIP: SELL cross but H1 above EMA50 ({h1_close:.2f} > {h1_ema_val:.2f})")
            return None

        # SL/TP calculation
        entry = close
        candle_low = df["low"].iloc[-1]
        candle_high = df["high"].iloc[-1]

        if direction == "BUY":
            raw_sl = candle_low - SL_ATR_MULT * atr_val
            sl_pips = (entry - raw_sl) / PIP_SIZE
            sl_pips = max(SL_MIN_PIPS, min(sl_pips, SL_MAX_PIPS))
            sl_price = entry - sl_pips * PIP_SIZE

            tp_pips = TP_ATR_MULT * atr_val / PIP_SIZE
            if tp_pips < sl_pips * MIN_RR:
                tp_pips = sl_pips * MIN_RR
            tp_price = entry + tp_pips * PIP_SIZE
        else:
            raw_sl = candle_high + SL_ATR_MULT * atr_val
            sl_pips = (raw_sl - entry) / PIP_SIZE
            sl_pips = max(SL_MIN_PIPS, min(sl_pips, SL_MAX_PIPS))
            sl_price = entry + sl_pips * PIP_SIZE

            tp_pips = TP_ATR_MULT * atr_val / PIP_SIZE
            if tp_pips < sl_pips * MIN_RR:
                tp_pips = sl_pips * MIN_RR
            tp_price = entry - tp_pips * PIP_SIZE

        sl_pips = round(sl_pips, 1)
        tp_pips = round(tp_pips, 1)

        reason = (
            f"EMA{EMA_FAST}/{EMA_SLOW} {signal_source} {direction.lower()}, "
            f"H1>{EMA_HTF}EMA {'up' if direction == 'BUY' else 'down'}, "
            f"ATR={atr_pips:.0f}p, SL={sl_pips:.0f}p, TP={tp_pips:.0f}p"
        )

        self._daily_trades += 1

        signal = Signal(
            direction=direction,
            symbol=self.symbol,
            entry_price=round(entry, 2),
            sl_price=round(sl_price, 2),
            tp_price=round(tp_price, 2),
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            reason=reason,
            confidence=0.8,
        )

        logger.info(f"EMA cross signal: {signal}")
        return signal

    def update_trade_result(self, pnl: float):
        """Called after a trade closes to update daily PnL for the 3rd-trade guard."""
        self._daily_pnl += pnl

    def _check_pullback_reentry(self, df: pd.DataFrame, ema8: pd.Series,
                                ema21: pd.Series, direction: str) -> bool:
        """
        After a confirmed EMA cross, if price pulls back to touch EMA8
        within the last 4 candles and trend is still intact — valid re-entry.
        """
        if len(df) < 5:
            return False
        last_4_idx = slice(-5, -1)
        ema8_now = ema8.iloc[-1]
        ema21_now = ema21.iloc[-1]
        price_now = df["close"].iloc[-1]

        if direction == "BUY":
            touched_ema8 = any(df["low"].iloc[last_4_idx].values <= ema8.iloc[last_4_idx].values * 1.0005)
            still_above_ema21 = price_now > ema21_now
            return touched_ema8 and still_above_ema21
        elif direction == "SELL":
            touched_ema8 = any(df["high"].iloc[last_4_idx].values >= ema8.iloc[last_4_idx].values * 0.9995)
            still_below_ema21 = price_now < ema21_now
            return touched_ema8 and still_below_ema21
        return False

    def _resample_h1(self, df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy().set_index("time")
        h1 = d.resample("1h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "tick_volume": "sum",
        }).dropna()
        return h1.reset_index()


if __name__ == "__main__":
    from backtest.data_loader import generate_synthetic_data

    df = generate_synthetic_data("XAUUSD", "2025-12-01", "2026-01-31", "M15", 2000)
    strat = EMACrossStrategy("XAUUSD", mode="backtest")
    count = {"BUY": 0, "SELL": 0}
    for i in range(60, len(df)):
        window = df.iloc[:i + 1]
        sig = strat.generate_signal(window)
        if sig:
            count[sig.direction] += 1
    print(f"Signals: BUY={count['BUY']}, SELL={count['SELL']}, total={sum(count.values())}")
