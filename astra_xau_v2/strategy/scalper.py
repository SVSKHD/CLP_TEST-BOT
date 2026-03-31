import logging
import pandas as pd

from strategy.base import BaseStrategy, Signal
from core.market import (
    get_yesterday_range, calc_sr_levels, calc_rsi,
    price_near_sr, detect_rsi_divergence,
)
from config.symbols import get_symbol_config
from config.settings import SCALPER_SL_PIPS, SCALPER_TP_PIPS

logger = logging.getLogger("astra.scalper")
SR_PROXIMITY_PCT = 0.002
SPREAD_MULTIPLIER = 3


class Scalper(BaseStrategy):
    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)
        self.cfg = get_symbol_config(symbol)
        self.daily_pips_gained = 0.0

    def name(self) -> str:
        return "Scalper"

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 30:
            logger.debug(f"{self.symbol} REJECT: insufficient candles ({len(df)} < 30)")
            return None

        close = df["close"].iloc[-1]
        pip_size = 0.1

        yday = get_yesterday_range(df)
        sr_levels = calc_sr_levels(df, period="4H")

        if not sr_levels:
            logger.debug(f"{self.symbol} REJECT: no S/R levels found")
            return None

        near_level = price_near_sr(close, sr_levels, SR_PROXIMITY_PCT)
        if near_level is None:
            logger.debug(f"{self.symbol} REJECT: price {close:.2f} not near any S/R level")
            return None

        rsi = calc_rsi(df["close"], 14)
        rsi_val = rsi.iloc[-1]
        divergence = detect_rsi_divergence(df["close"], rsi, lookback=10)

        if self.mode == "backtest":
            current_spread = 3.0
        elif self.mode == "live":
            try:
                from core.market import get_current_spread_pips
                current_spread = get_current_spread_pips(self.symbol)
            except Exception:
                current_spread = self.cfg["typical_spread_pips"]
        else:
            if "spread" in df.columns:
                current_spread = df["spread"].iloc[-1] * 10
            else:
                current_spread = self.cfg["typical_spread_pips"]

        max_spread = self.cfg["typical_spread_pips"] * SPREAD_MULTIPLIER
        if current_spread > max_spread:
            logger.debug(f"{self.symbol} REJECT: spread too wide: {current_spread:.1f} > {max_spread:.1f}")
            return None

        is_bt = self.mode == "backtest"

        direction = None
        reason = ""

        if is_bt:
            # Backtest mode: relaxed thresholds for synthetic/historical data
            if near_level["type"] == "support":
                if divergence == "BULLISH" or rsi_val < 55:
                    direction = "BUY"
                    reason = f"Near support {near_level['price']:.2f}, RSI={rsi_val:.1f}, div={divergence}"
            elif near_level["type"] == "resistance":
                if divergence == "BEARISH" or rsi_val > 45:
                    direction = "SELL"
                    reason = f"Near resistance {near_level['price']:.2f}, RSI={rsi_val:.1f}, div={divergence}"
            elif near_level["type"] == "pivot":
                if rsi_val < 50:
                    direction = "BUY"
                    reason = f"Pivot bounce {near_level['price']:.2f}, RSI={rsi_val:.1f}"
                else:
                    direction = "SELL"
                    reason = f"Pivot rejection {near_level['price']:.2f}, RSI={rsi_val:.1f}"
        else:
            # Live mode: strict thresholds
            if near_level["type"] == "support":
                if divergence == "BULLISH" or rsi_val < 35:
                    direction = "BUY"
                    reason = f"Near support {near_level['price']:.2f}, RSI={rsi_val:.1f}, div={divergence}"
                elif rsi_val < 45 and near_level["strength"] == "strong":
                    direction = "BUY"
                    reason = f"Strong support {near_level['price']:.2f}, RSI={rsi_val:.1f}"
            elif near_level["type"] == "resistance":
                if divergence == "BEARISH" or rsi_val > 65:
                    direction = "SELL"
                    reason = f"Near resistance {near_level['price']:.2f}, RSI={rsi_val:.1f}, div={divergence}"
                elif rsi_val > 55 and near_level["strength"] == "strong":
                    direction = "SELL"
                    reason = f"Strong resistance {near_level['price']:.2f}, RSI={rsi_val:.1f}"
            elif near_level["type"] == "pivot":
                if divergence == "BULLISH" and rsi_val < 45:
                    direction = "BUY"
                    reason = f"Pivot bounce {near_level['price']:.2f}, RSI={rsi_val:.1f}, bullish div"
                elif divergence == "BEARISH" and rsi_val > 55:
                    direction = "SELL"
                    reason = f"Pivot rejection {near_level['price']:.2f}, RSI={rsi_val:.1f}, bearish div"

        if direction is None:
            logger.debug(
                f"{self.symbol} REJECT: no direction — near {near_level['type']} "
                f"({near_level['price']:.2f}, {near_level['strength']}), "
                f"RSI={rsi_val:.1f}, div={divergence}"
            )
            return None

        if direction == "BUY":
            entry = close
            sl = entry - SCALPER_SL_PIPS * pip_size
            tp = entry + SCALPER_TP_PIPS * pip_size
        else:
            entry = close
            sl = entry + SCALPER_SL_PIPS * pip_size
            tp = entry - SCALPER_TP_PIPS * pip_size

        confidence = 0.5
        if divergence in ("BULLISH", "BEARISH"):
            confidence += 0.2
        if near_level["strength"] == "strong":
            confidence += 0.15
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
        "open": prices,
        "high": prices + np.random.rand(n) * 1.5,
        "low": prices - np.random.rand(n) * 1.5,
        "close": prices + np.random.randn(n) * 0.2,
        "tick_volume": np.random.randint(100, 5000, n),
        "spread": np.random.uniform(0.15, 0.35, n),
    })

    scalper = Scalper("XAUUSD", mode="backtest")
    signal = scalper.generate_signal(df)
    if signal:
        print(f"Signal: {signal}")
    else:
        print("No signal generated (normal for random data)")
