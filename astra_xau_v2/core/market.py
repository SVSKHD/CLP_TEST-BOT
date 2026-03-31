import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

logger = logging.getLogger("astra.market")


def fetch_candles_live(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    from core.mt5_client import copy_rates
    rates = copy_rates(symbol, timeframe, datetime.utcnow(), count)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"tick_volume": "tick_volume"}, inplace=True)
    return df


def fetch_candles_range(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    from core.mt5_client import copy_rates_range
    rates = copy_rates_range(symbol, timeframe, start, end)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_yesterday_range(df: pd.DataFrame) -> dict:
    today = df["time"].iloc[-1].date()
    yesterday = today - timedelta(days=1)
    yday = df[df["time"].dt.date == yesterday]
    if yday.empty:
        yday = df[df["time"].dt.date < today].tail(96)
    if yday.empty:
        return {"high": None, "low": None, "range_pips": 0}
    h = yday["high"].max()
    l = yday["low"].min()
    return {"high": h, "low": l, "range_pips": (h - l) * 10}


def calc_sr_levels(df: pd.DataFrame, period: str = "4H") -> list:
    if period == "4H":
        interval = 16
    elif period == "1H":
        interval = 4
    else:
        interval = 16

    recent = df.tail(interval)
    if recent.empty:
        return []

    levels = []
    close = recent["close"].iloc[-1]
    high = recent["high"].max()
    low = recent["low"].min()
    mid = (high + low) / 2

    levels.append({"price": high, "type": "resistance", "strength": "strong"})
    levels.append({"price": low, "type": "support", "strength": "strong"})
    levels.append({"price": mid, "type": "pivot", "strength": "moderate"})

    q75 = recent["high"].quantile(0.75)
    q25 = recent["low"].quantile(0.25)
    levels.append({"price": q75, "type": "resistance", "strength": "moderate"})
    levels.append({"price": q25, "type": "support", "strength": "moderate"})

    return sorted(levels, key=lambda x: x["price"])


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.inf)
    return 100 - (100 / (1 + rs))


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.inf)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx


def get_current_spread_pips(symbol: str) -> float:
    from core.mt5_client import get_symbol_info
    info = get_symbol_info(symbol)
    return info["spread"] * info["point"] * 10


def price_near_sr(price: float, sr_levels: list, threshold_pct: float = 0.002) -> dict:
    for level in sr_levels:
        dist = abs(price - level["price"]) / price
        if dist <= threshold_pct:
            return level
    return None


def detect_rsi_divergence(prices: pd.Series, rsi: pd.Series, lookback: int = 10) -> str:
    if len(prices) < lookback or len(rsi) < lookback:
        return "NONE"

    recent_prices = prices.iloc[-lookback:]
    recent_rsi = rsi.iloc[-lookback:]

    price_low_idx = recent_prices.idxmin()
    price_high_idx = recent_prices.idxmax()

    if (price_low_idx == recent_prices.index[-1] and
            recent_rsi.iloc[-1] > recent_rsi.loc[price_low_idx] if price_low_idx in recent_rsi.index else False):
        return "BULLISH"

    if (price_high_idx == recent_prices.index[-1] and
            recent_rsi.iloc[-1] < recent_rsi.loc[price_high_idx] if price_high_idx in recent_rsi.index else False):
        return "BEARISH"

    price_lows = recent_prices.nsmallest(2)
    if len(price_lows) >= 2:
        idx1, idx2 = price_lows.index[0], price_lows.index[1]
        if idx1 in recent_rsi.index and idx2 in recent_rsi.index:
            if price_lows.iloc[1] <= price_lows.iloc[0] and recent_rsi.loc[idx2] > recent_rsi.loc[idx1]:
                return "BULLISH"

    price_highs = recent_prices.nlargest(2)
    if len(price_highs) >= 2:
        idx1, idx2 = price_highs.index[0], price_highs.index[1]
        if idx1 in recent_rsi.index and idx2 in recent_rsi.index:
            if price_highs.iloc[1] >= price_highs.iloc[0] and recent_rsi.loc[idx2] < recent_rsi.loc[idx1]:
                return "BEARISH"

    return "NONE"


if __name__ == "__main__":
    np.random.seed(42)
    n = 200
    prices = 2000 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
        "open": prices,
        "high": prices + np.random.rand(n) * 2,
        "low": prices - np.random.rand(n) * 2,
        "close": prices + np.random.randn(n) * 0.3,
        "tick_volume": np.random.randint(100, 5000, n),
    })

    print("Yesterday range:", get_yesterday_range(df))
    print("S/R levels:", calc_sr_levels(df))
    rsi = calc_rsi(df["close"])
    print(f"RSI last: {rsi.iloc[-1]:.2f}")
    ema = calc_ema(df["close"], 50)
    print(f"EMA50 last: {ema.iloc[-1]:.2f}")
    atr = calc_atr(df)
    print(f"ATR14 last: {atr.iloc[-1]:.2f}")
