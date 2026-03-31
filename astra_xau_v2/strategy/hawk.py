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
        ema50_prev = ema50.iloc[-2]
        close_current = df["close"].iloc[-1]
        close_prev = df["close"].iloc[-2]

        ema_cross_up = close_prev < ema50_prev and close_current > ema50_current
        ema_cross_down = close_prev > ema50_prev and close_current < ema50_current
        price_above_ema = close_current > ema50_current
        price_below_ema = close_current < ema50_current

        # Regime detection: % of last 50 candles closing above EMA50
        lookback = min(50, len(df))
        recent_close = df["close"].iloc[-lookback:]
        recent_ema = ema50.iloc[-lookback:]
        above_pct = (recent_close.values > recent_ema.values).sum() / lookback * 100
        if above_pct > 75:
            regime = "BULL"
        elif above_pct < 25:
            regime = "BEAR"
        else:
            regime = "RANGING"

        logger.info(
            f"Hawk {self.symbol}: regime={regime} ({above_pct:.0f}% above EMA50), "
            f"signal={signal.direction}, price={close_current:.2f}, ema50={ema50_current:.2f}"
        )

        # In trending regimes, counter-trend signals need high confidence
        if regime == "BULL" and signal.direction == "SELL":
            if signal.confidence >= 0.65:
                logger.info(f"Hawk {self.symbol}: allowing counter-trend SELL in BULL (conf={signal.confidence:.2f})")
            else:
                return FilterResult(FilterResult.REJECT,
                                    f"SELL rejected: BULL regime ({above_pct:.0f}% above EMA50), low conf={signal.confidence:.2f}")
        if regime == "BEAR" and signal.direction == "BUY":
            if signal.confidence >= 0.65:
                logger.info(f"Hawk {self.symbol}: allowing counter-trend BUY in BEAR (conf={signal.confidence:.2f})")
            else:
                return FilterResult(FilterResult.REJECT,
                                    f"BUY rejected: BEAR regime ({above_pct:.0f}% above EMA50), low conf={signal.confidence:.2f}")

        h1_df = self._resample_h1(df)
        if len(h1_df) >= 14:
            atr_h1 = calc_atr(h1_df, 14)
            atr_val = atr_h1.iloc[-1]
            atr_pips = atr_val * 10

            atr_threshold = ATR_DEAD_MARKET_PIPS_BT if self.mode == "backtest" else ATR_DEAD_MARKET_PIPS_LIVE
            if atr_pips < atr_threshold:
                return FilterResult(FilterResult.REJECT,
                                    f"Dead market: ATR(H1)={atr_pips:.1f} pips < {atr_threshold}")
        else:
            atr_pips = 0

        # In RANGING regime, the scalper's mean-reversion signals are valid
        # regardless of EMA position — confirm if ATR shows the market is alive
        if regime == "RANGING":
            return FilterResult(FilterResult.CONFIRM,
                                f"{signal.direction} confirmed: RANGING regime, mean-reversion OK, ATR={atr_pips:.1f}")

        # In trending regimes (BULL/BEAR), require EMA alignment for trend-with entries
        if len(h1_df) >= 14:
            h1_ema50 = calc_ema(h1_df["close"], min(50, len(h1_df) - 1))
            h1_trend_up = h1_df["close"].iloc[-1] > h1_ema50.iloc[-1]
            h1_trend_down = h1_df["close"].iloc[-1] < h1_ema50.iloc[-1]
        else:
            h1_trend_up = True
            h1_trend_down = True

        if signal.direction == "BUY":
            if (ema_cross_up or price_above_ema) and h1_trend_up:
                return FilterResult(FilterResult.CONFIRM,
                                    f"BUY confirmed: {regime} regime, M15 EMA50 bullish, H1 trend up, ATR={atr_pips:.1f}")
            elif price_above_ema:
                return FilterResult(FilterResult.CONFIRM,
                                    f"BUY confirmed: {regime} regime, price above EMA50, ATR={atr_pips:.1f}")
            else:
                return FilterResult(FilterResult.REJECT,
                                    f"BUY rejected: price below EMA50 ({close_current:.2f} < {ema50_current:.2f}), {regime}")

        elif signal.direction == "SELL":
            if (ema_cross_down or price_below_ema) and h1_trend_down:
                return FilterResult(FilterResult.CONFIRM,
                                    f"SELL confirmed: {regime} regime, M15 EMA50 bearish, H1 trend down, ATR={atr_pips:.1f}")
            elif price_below_ema:
                return FilterResult(FilterResult.CONFIRM,
                                    f"SELL confirmed: {regime} regime, price below EMA50, ATR={atr_pips:.1f}")
            else:
                return FilterResult(FilterResult.REJECT,
                                    f"SELL rejected: price above EMA50 ({close_current:.2f} > {ema50_current:.2f}), {regime}")

        return FilterResult(FilterResult.MONITOR, "Unknown direction")

    def _in_session(self, df: pd.DataFrame) -> bool:
        last_time = df["time"].iloc[-1]
        if isinstance(last_time, pd.Timestamp):
            hour = last_time.hour
        else:
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
