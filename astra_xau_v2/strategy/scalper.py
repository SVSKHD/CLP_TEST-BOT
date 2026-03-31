import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from strategy.base import BaseStrategy, Signal
from strategy.momentum_price import MomentumPriceStrategy

logger = logging.getLogger("astra.scalper")

PIP_SIZE = 0.1


class Scalper(BaseStrategy):
    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)
        self.strategy = MomentumPriceStrategy(lot_size=2.0, logger_inst=logger)

    def name(self) -> str:
        return "MomentumPrice"

    def generate_signal(self, df: pd.DataFrame, current_time: datetime = None) -> Optional[Signal]:
        mom_signal = self.strategy.get_signal(df, current_time_utc=current_time, mode=self.mode)
        if mom_signal is None:
            return None

        # Convert MomentumSignal → base Signal for backtest/executor compatibility
        sl_pips = abs(mom_signal.entry - mom_signal.sl) / PIP_SIZE
        tp_pips = abs(mom_signal.tp - mom_signal.entry) / PIP_SIZE

        return Signal(
            direction=mom_signal.direction,
            symbol=self.symbol,
            entry_price=round(mom_signal.entry, 2),
            sl_price=round(mom_signal.sl, 2),
            tp_price=round(mom_signal.tp, 2),
            sl_pips=round(sl_pips, 1),
            tp_pips=round(tp_pips, 1),
            reason=(
                f"Momentum {mom_signal.direction}: "
                f"trigger=${abs(mom_signal.trigger_move):.2f}, "
                f"session=${abs(mom_signal.session_move):.2f}, "
                f"strong={mom_signal.strong_candles}"
            ),
            confidence=0.8,
        )


if __name__ == "__main__":
    import numpy as np

    np.random.seed(42)
    n = 300
    prices = 2000.0 + np.cumsum(np.random.randn(n) * 0.5)

    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01 07:00", periods=n, freq="5min"),
        "open": prices - np.random.randn(n) * 0.1,
        "high": prices + np.random.rand(n) * 2,
        "low": prices - np.random.rand(n) * 2,
        "close": prices,
        "tick_volume": np.random.randint(100, 5000, n),
    })

    scalper = Scalper("XAUUSD", mode="backtest")
    signal = scalper.generate_signal(df)
    print(f"Signal: {signal}" if signal else "No signal")
