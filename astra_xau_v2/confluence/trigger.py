import logging
import pandas as pd
from core.market import calc_atr, calc_ema

logger = logging.getLogger("astra.trigger")


def detect_trigger(df: pd.DataFrame) -> dict:
    if len(df) < 52:
        return {"triggered": False, "pattern": None, "direction": None}

    atr = calc_atr(df, 14)
    atr_val = atr.iloc[-1]
    ema50 = calc_ema(df["close"], 50)
    ema_val = ema50.iloc[-1]

    if atr_val <= 0:
        return {"triggered": False, "pattern": None, "direction": None}

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    close = curr["close"]
    open_ = curr["open"]
    prev_close = prev["close"]
    prev_open = prev["open"]

    ema_dist = abs(close - ema_val) / atr_val

    # --- BUY trigger: pullback to EMA in uptrend ---
    if close > ema_val and ema_dist < 0.8:
        bullish_candle = close > open_
        higher_low = curr["low"] > prev["low"]

        if bullish_candle and higher_low:
            return {
                "triggered": True,
                "pattern": "ema_pullback_buy",
                "direction": "BUY",
            }

    # --- SELL trigger: pullback to EMA in downtrend ---
    if close < ema_val and ema_dist < 0.8:
        bearish_candle = close < open_
        lower_high = curr["high"] < prev["high"]

        if bearish_candle and lower_high:
            return {
                "triggered": True,
                "pattern": "ema_pullback_sell",
                "direction": "SELL",
            }

    # --- Bullish engulfing at demand zone ---
    c_body = abs(close - open_)
    if (close > open_ and prev_close < prev_open and
            close > prev_open and open_ < prev_close and
            c_body > 0.4 * atr_val):
        return {"triggered": True, "pattern": "bullish_engulfing", "direction": "BUY"}

    # --- Bearish engulfing at supply zone ---
    if (close < open_ and prev_close > prev_open and
            close < prev_open and open_ > prev_close and
            c_body > 0.4 * atr_val):
        return {"triggered": True, "pattern": "bearish_engulfing", "direction": "SELL"}

    return {"triggered": False, "pattern": None, "direction": None}


if __name__ == "__main__":
    from backtest.data_loader import generate_synthetic_data
    df = generate_synthetic_data("XAUUSD", "2025-12-01", "2026-01-15", "M15", 2000)
    count = {"BUY": 0, "SELL": 0}
    for i in range(60, len(df)):
        window = df.iloc[:i + 1]
        result = detect_trigger(window)
        if result["triggered"]:
            count[result["direction"]] += 1
    print(f"Triggers: BUY={count['BUY']}, SELL={count['SELL']}")
