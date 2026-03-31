import os
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger("astra.data_loader")

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "history")


def load_history(symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    csv_path = os.path.join(HISTORY_DIR, f"{symbol}_{timeframe}.csv")

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    if os.path.exists(csv_path):
        df = _load_csv(csv_path)
        if df is not None and len(df) > 0:
            csv_start = df["time"].min()
            csv_end = df["time"].max()
            if csv_start <= pd.Timestamp(start_dt) and csv_end >= pd.Timestamp(end_dt):
                mask = (df["time"] >= pd.Timestamp(start_dt)) & (df["time"] <= pd.Timestamp(end_dt))
                filtered = df[mask].reset_index(drop=True)
                logger.info(f"Loaded {len(filtered)} candles from CSV: {csv_path}")
                return filtered
            logger.info(f"CSV data incomplete ({csv_start} to {csv_end}), fetching from MT5")

    try:
        df = _fetch_from_mt5(symbol, timeframe, start_dt, end_dt)
        _save_csv(df, csv_path)
        return df
    except Exception as e:
        logger.warning(f"MT5 fetch failed: {e}")

    if os.path.exists(csv_path):
        df = _load_csv(csv_path)
        if df is not None:
            logger.warning("Using existing CSV despite date range mismatch")
            return df

    raise RuntimeError(
        f"Cannot load history for {symbol} ({start} to {end}). "
        f"Place a CSV file at {csv_path} with columns: time,open,high,low,close,tick_volume,spread"
    )


def _load_csv(path: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, parse_dates=["time"])
        required = ["time", "open", "high", "low", "close"]
        for col in required:
            if col not in df.columns:
                logger.warning(f"CSV missing column: {col}")
                return None
        if "tick_volume" not in df.columns:
            df["tick_volume"] = 0
        if "spread" not in df.columns:
            df["spread"] = 0
        return df.sort_values("time").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"CSV load error: {e}")
        return None


def _fetch_from_mt5(symbol: str, timeframe: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    from core.mt5_client import copy_rates_range
    rates = copy_rates_range(symbol, timeframe, start_dt, end_dt)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    if "tick_volume" not in df.columns:
        df["tick_volume"] = 0
    if "spread" not in df.columns:
        df["spread"] = 0
    logger.info(f"Fetched {len(df)} candles from MT5: {symbol} {timeframe}")
    return df


def _save_csv(df: pd.DataFrame, path: str):
    try:
        df.to_csv(path, index=False)
        logger.info(f"Saved history CSV: {path}")
    except Exception as e:
        logger.warning(f"CSV save failed: {e}")


def generate_synthetic_data(symbol: str, start: str, end: str,
                            timeframe: str = "M15", base_price: float = 2000.0) -> pd.DataFrame:
    import numpy as np
    # Deterministic seed from symbol name (hash() varies between Python processes)
    seed = sum(ord(c) * (i + 1) for i, c in enumerate(symbol))
    np.random.seed(seed % 2**31)

    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    freq_map = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
                "H1": "1h", "H4": "4h", "D1": "1D"}
    freq = freq_map.get(timeframe, "15min")

    times = pd.date_range(start_dt, end_dt, freq=freq)
    times = times[times.dayofweek < 5]
    n = len(times)

    if n == 0:
        raise ValueError(f"No candles for {start} to {end}")

    # Ornstein-Uhlenbeck mean-reverting process for realistic S/R bounces
    # Scale volatility by timeframe — M5 needs higher per-bar vol than M15
    vol_scale_map = {"M1": 0.0008, "M5": 0.0018, "M15": 0.0013, "M30": 0.0010,
                     "H1": 0.0008, "H4": 0.0006, "D1": 0.0004}
    mean_reversion_speed = 0.010
    volatility_scale = vol_scale_map.get(timeframe, 0.0013)
    trend_drift = 0.000008

    log_prices = np.zeros(n)
    log_prices[0] = 0.0
    moving_mean = 0.0
    for j in range(1, n):
        shock = np.random.randn() * volatility_scale
        moving_mean += trend_drift
        log_prices[j] = (log_prices[j - 1]
                         + mean_reversion_speed * (moving_mean - log_prices[j - 1])
                         + shock)

    prices = base_price * np.exp(log_prices)
    # Close tracks next price (momentum) so candle bodies are strong
    # close[i] is biased toward prices[i+1] direction
    closes = np.zeros(n)
    opens = prices.copy()
    for j in range(n - 1):
        direction = prices[j + 1] - prices[j]
        closes[j] = prices[j] + direction * np.random.uniform(0.5, 1.0)
    closes[-1] = prices[-1] + np.random.randn() * 0.3

    # Wicks: small relative to body to keep body ratio > 60%
    wick_up = np.abs(np.random.randn(n)) * 0.2
    wick_down = np.abs(np.random.randn(n)) * 0.2
    highs = np.maximum(opens, closes) + wick_up
    lows = np.minimum(opens, closes) - wick_down

    df = pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": np.random.randint(100, 10000, n),
        "spread": np.random.randint(15, 40, n),
    })

    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)

    return df


if __name__ == "__main__":
    df = generate_synthetic_data("XAUUSD", "2025-01-01", "2025-01-31")
    print(f"Synthetic XAUUSD: {len(df)} candles")
    print(df.head())
    print(f"Price range: {df['low'].min():.2f} - {df['high'].max():.2f}")
