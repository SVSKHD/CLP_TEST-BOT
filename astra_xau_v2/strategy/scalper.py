import logging
import pandas as pd

from strategy.base import BaseStrategy, Signal
from core.market import calc_rsi, calc_ema, calc_atr
from config.symbols import get_symbol_config
from config.settings import SCALPER_SL_PIPS, SCALPER_TP_PIPS

logger = logging.getLogger("astra.scalper")

SPREAD_MULTIPLIER = 3


class Scalper(BaseStrategy):
    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)
        self.cfg = get_symbol_config(symbol)

    def name(self) -> str:
        return "Scalper"

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 60:
            logger.debug(f"{self.symbol} REJECT: insufficient candles ({len(df)} < 60)")
            return None

        pip_size = 0.1
        close = df["close"].iloc[-1]
        open_ = df["open"].iloc[-1]
        prev_low = df["low"].iloc[-2]
        prev_high = df["high"].iloc[-2]
        curr_low = df["low"].iloc[-1]
        curr_high = df["high"].iloc[-1]

        # Core indicators
        ema50 = calc_ema(df["close"], 50)
        ema50_val = ema50.iloc[-1]
        rsi = calc_rsi(df["close"], 14)
        rsi_val = rsi.iloc[-1]
        atr = calc_atr(df, 14)
        atr_val = atr.iloc[-1]

        if atr_val <= 0:
            logger.debug(f"{self.symbol} REJECT: ATR is zero")
            return None

        # Distance from EMA50 in ATR units
        ema_distance = abs(close - ema50_val) / atr_val
        pullback_threshold = 0.6

        # Spread check
        if self.mode == "backtest":
            current_spread = 3.0
        elif self.mode == "live":
            try:
                from core.market import get_current_spread_pips
                current_spread = get_current_spread_pips(self.symbol)
            except Exception:
                current_spread = self.cfg["typical_spread_pips"]
        else:
            current_spread = self.cfg["typical_spread_pips"]

        max_spread = self.cfg["typical_spread_pips"] * SPREAD_MULTIPLIER
        if current_spread > max_spread:
            logger.debug(f"{self.symbol} REJECT: spread too wide: {current_spread:.1f} > {max_spread:.1f}")
            return None

        direction = None
        reason = ""

        # --- BUY signal: trend-pullback in uptrend ---
        if close > ema50_val:
            bullish_candle = close > open_
            higher_low = curr_low > prev_low
            rsi_neutral = 40 <= rsi_val <= 60
            near_ema = ema_distance <= pullback_threshold

            if bullish_candle and higher_low and rsi_neutral and near_ema:
                direction = "BUY"
                reason = (
                    f"Pullback to EMA50 in uptrend: price={close:.2f}, "
                    f"ema50={ema50_val:.2f}, dist={ema_distance:.2f}ATR, "
                    f"RSI={rsi_val:.1f}, higher low"
                )
            else:
                fails = []
                if not bullish_candle:
                    fails.append("bearish candle")
                if not higher_low:
                    fails.append("no higher low")
                if not rsi_neutral:
                    fails.append(f"RSI={rsi_val:.1f} outside 40-60")
                if not near_ema:
                    fails.append(f"too far from EMA ({ema_distance:.2f}ATR)")
                logger.debug(
                    f"{self.symbol} BUY conditions not met (uptrend): {', '.join(fails)}"
                )

        # --- SELL signal: trend-pullback in downtrend ---
        elif close < ema50_val:
            bearish_candle = close < open_
            lower_high = curr_high < prev_high
            rsi_neutral = 40 <= rsi_val <= 60
            near_ema = ema_distance <= pullback_threshold

            if bearish_candle and lower_high and rsi_neutral and near_ema:
                direction = "SELL"
                reason = (
                    f"Pullback to EMA50 in downtrend: price={close:.2f}, "
                    f"ema50={ema50_val:.2f}, dist={ema_distance:.2f}ATR, "
                    f"RSI={rsi_val:.1f}, lower high"
                )
            else:
                fails = []
                if not bearish_candle:
                    fails.append("bullish candle")
                if not lower_high:
                    fails.append("no lower high")
                if not rsi_neutral:
                    fails.append(f"RSI={rsi_val:.1f} outside 40-60")
                if not near_ema:
                    fails.append(f"too far from EMA ({ema_distance:.2f}ATR)")
                logger.debug(
                    f"{self.symbol} SELL conditions not met (downtrend): {', '.join(fails)}"
                )
        else:
            logger.debug(f"{self.symbol} REJECT: price exactly at EMA50")

        if direction is None:
            return None

        # Build signal
        if direction == "BUY":
            entry = close
            sl = entry - SCALPER_SL_PIPS * pip_size
            tp = entry + SCALPER_TP_PIPS * pip_size
        else:
            entry = close
            sl = entry + SCALPER_SL_PIPS * pip_size
            tp = entry - SCALPER_TP_PIPS * pip_size

        # Confidence scoring
        confidence = 0.5
        if ema_distance < 0.2:
            confidence += 0.15  # very close to EMA = strong pullback
        if 45 <= rsi_val <= 55:
            confidence += 0.1   # centered RSI = balanced momentum
        if current_spread < self.cfg["typical_spread_pips"]:
            confidence += 0.1

        signal = Signal(
            direction=direction,
            symbol=self.symbol,
            entry_price=entry,
            sl_price=sl,
            tp_price=tp,
            sl_pips=SCALPER_SL_PIPS,
            tp_pips=SCALPER_TP_PIPS,
            reason=reason,
            confidence=min(confidence, 1.0),
        )

        logger.info(f"Scalper signal: {signal}")
        return signal


if __name__ == "__main__":
    import numpy as np

    np.random.seed(42)
    n = 200
    base = 2000.0
    prices = base + np.cumsum(np.random.randn(n) * 0.3)

    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
        "open": prices - np.random.randn(n) * 0.1,
        "high": prices + np.random.rand(n) * 1.5,
        "low": prices - np.random.rand(n) * 1.5,
        "close": prices + np.random.randn(n) * 0.2,
        "tick_volume": np.random.randint(100, 5000, n),
    })

    scalper = Scalper("XAUUSD", mode="backtest")
    signal = scalper.generate_signal(df)
    if signal:
        print(f"Signal: {signal}")
    else:
        print("No signal generated (normal for random data)")
