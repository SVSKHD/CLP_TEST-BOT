import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from strategy.base import BaseStrategy, Signal
from strategy.ema_cross import EMACrossStrategy

logger = logging.getLogger("astra.scalper")


class Scalper(BaseStrategy):
    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)
        self.strategy = EMACrossStrategy(symbol, mode)

    def name(self) -> str:
        return "EMACross"

    def generate_signal(self, df: pd.DataFrame, current_time: datetime = None) -> Optional[Signal]:
        return self.strategy.generate_signal(df, current_time=current_time)


if __name__ == "__main__":
    import numpy as np

    np.random.seed(42)
    n = 300
    prices = 2000.0 + np.cumsum(np.random.randn(n) * 0.5)

    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
        "open": prices - np.random.randn(n) * 0.1,
        "high": prices + np.random.rand(n) * 2,
        "low": prices - np.random.rand(n) * 2,
        "close": prices,
        "tick_volume": np.random.randint(100, 5000, n),
    })

    scalper = Scalper("XAUUSD", mode="backtest")
    signal = scalper.generate_signal(df)
    print(f"Signal: {signal}" if signal else "No signal")
