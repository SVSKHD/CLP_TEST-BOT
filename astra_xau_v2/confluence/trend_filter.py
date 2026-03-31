import logging
import pandas as pd
from core.market import calc_ema

logger = logging.getLogger("astra.trend_filter")

EMA_PERIOD = 20
SLOPE_LOOKBACK = 5


def get_h1_trend(df_m15: pd.DataFrame) -> dict:
    h1 = _resample_h1(df_m15)
    if len(h1) < EMA_PERIOD + SLOPE_LOOKBACK:
        return {"direction": "FLAT", "slope": 0.0, "ema": 0.0}

    ema20 = calc_ema(h1["close"], EMA_PERIOD)
    current_ema = ema20.iloc[-1]
    prev_ema = ema20.iloc[-SLOPE_LOOKBACK]

    slope = (current_ema - prev_ema) / SLOPE_LOOKBACK

    if slope > 0.05:
        direction = "UP"
    elif slope < -0.05:
        direction = "DOWN"
    else:
        direction = "FLAT"

    logger.debug(f"H1 trend: {direction}, slope={slope:.4f}, ema20={current_ema:.2f}")
    return {"direction": direction, "slope": slope, "ema": current_ema}


def slope_matches_direction(trend: dict, direction: str) -> bool:
    if direction == "BUY" and trend["direction"] == "UP":
        return True
    if direction == "SELL" and trend["direction"] == "DOWN":
        return True
    return False


def _resample_h1(df_m15: pd.DataFrame) -> pd.DataFrame:
    df = df_m15.copy().set_index("time")
    h1 = df.resample("1h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "tick_volume": "sum",
    }).dropna()
    return h1.reset_index()


if __name__ == "__main__":
    from backtest.data_loader import generate_synthetic_data
    df = generate_synthetic_data("XAUUSD", "2025-12-01", "2026-01-15", "M15", 2000)
    trend = get_h1_trend(df)
    print(f"H1 Trend: {trend}")
