import logging
from datetime import datetime
import pandas as pd

from strategy.base import BaseFilter, FilterResult, Signal
from core.market import calc_ema, calc_atr

logger = logging.getLogger("astra.hawk")

ATR_DEAD_MARKET_PIPS_LIVE = 5.0
ATR_DEAD_MARKET_PIPS_BT = 1.0
LONDON_START = 7
LONDON_END = 12
NY_START = 13
NY_END = 17


class HawkFilter(BaseFilter):
    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)

    def name(self) -> str:
        return "Hawk"

    def evaluate(self, df: pd.DataFrame, signal: Signal) -> FilterResult:
        if len(df) < 60:
            return FilterResult(FilterResult.MONITOR, "Insufficient data for Hawk filter")

        if self.mode != "backtest" and not self._in_session(df):
            return FilterResult(FilterResult.REJECT, "Outside trading session (London/NY)")

        ema50 = calc_ema(df["close"], 50)
        ema50_current = ema50.iloc[-1]
        close_current = df["close"].iloc[-1]

        atr14 = calc_atr(df, 14)
        atr_val = atr14.iloc[-1]
        atr_pips = atr_val * 10

        # Dead market check
        atr_threshold = ATR_DEAD_MARKET_PIPS_BT if self.mode == "backtest" else ATR_DEAD_MARKET_PIPS_LIVE
        if atr_val > 0 and atr_pips < atr_threshold:
            return FilterResult(FilterResult.REJECT,
                                f"Dead market: ATR={atr_pips:.1f} pips < {atr_threshold}")

        # Regime detection using current price position relative to EMA50
        if atr_val > 0:
            bull_strength = (close_current - ema50_current) / atr_val
        else:
            bull_strength = 0.0

        if bull_strength > 0.5:
            regime = "BULL"
        elif bull_strength < -0.5:
            regime = "BEAR"
        else:
            regime = "RANGING"

        logger.info(
            f"Hawk {self.symbol}: regime={regime} (strength={bull_strength:.2f}), "
            f"signal={signal.direction}, price={close_current:.2f}, "
            f"ema50={ema50_current:.2f}, ATR={atr_pips:.1f}"
        )

        # Regime enforcement
        if regime == "BULL" and signal.direction == "SELL":
            return FilterResult(FilterResult.REJECT,
                                f"SELL rejected: BULL regime (strength={bull_strength:.2f})")

        if regime == "BEAR" and signal.direction == "BUY":
            return FilterResult(FilterResult.REJECT,
                                f"BUY rejected: BEAR regime (strength={bull_strength:.2f})")

        if regime == "RANGING" and signal.confidence < 0.65:
            return FilterResult(FilterResult.REJECT,
                                f"{signal.direction} rejected: RANGING regime, low conf={signal.confidence:.2f}")

        # Signal aligns with regime — confirm
        return FilterResult(FilterResult.CONFIRM,
                            f"{signal.direction} confirmed: {regime} regime, "
                            f"strength={bull_strength:.2f}, ATR={atr_pips:.1f}")

    def _in_session(self, df: pd.DataFrame) -> bool:
        last_time = df["time"].iloc[-1]
        hour = last_time.hour
        if LONDON_START <= hour < LONDON_END:
            return True
        if NY_START <= hour < NY_END:
            return True
        return False

    def _resample_h1(self, df: pd.DataFrame) -> pd.DataFrame:
        df_copy = df.copy()
        df_copy = df_copy.set_index("time")
        h1 = df_copy.resample("1h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "tick_volume": "sum",
        }).dropna()
        h1 = h1.reset_index()
        return h1


if __name__ == "__main__":
    import numpy as np
    from strategy.base import Signal

    np.random.seed(42)
    n = 300
    base = 2000.0
    trend = np.linspace(0, 15, n)
    noise = np.cumsum(np.random.randn(n) * 0.3)
    prices = base + trend + noise

    start = datetime(2025, 1, 2, 8, 0)
    df = pd.DataFrame({
        "time": pd.date_range(start, periods=n, freq="15min"),
        "open": prices,
        "high": prices + np.random.rand(n) * 2,
        "low": prices - np.random.rand(n) * 2,
        "close": prices + np.random.randn(n) * 0.2,
        "tick_volume": np.random.randint(100, 5000, n),
    })

    hawk = HawkFilter("XAUUSD", mode="backtest")
    test_signal = Signal("BUY", "XAUUSD", 2010.0, 2006.0, 2016.0, 40, 60, "test")
    result = hawk.evaluate(df, test_signal)
    print(f"Hawk result: {result}")
