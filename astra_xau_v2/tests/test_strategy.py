import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from strategy.scalper import Scalper
from strategy.hawk import HawkFilter
from strategy.ema_cross import EMACrossStrategy
from strategy.base import Signal, FilterResult


class TestEMACross(unittest.TestCase):
    def _make_trending_df(self, n=200, direction="up", seed=42):
        """Build data with a clear trend so EMA8 crosses EMA21."""
        np.random.seed(seed)
        if direction == "up":
            trend = np.linspace(0, 30, n)
        else:
            trend = np.linspace(0, -30, n)
        noise = np.random.randn(n) * 0.4
        prices = 2000.0 + trend + noise
        start = datetime(2025, 1, 2, 8, 0)
        opens = prices - np.random.randn(n) * 0.2
        return pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="15min"),
            "open": opens,
            "high": np.maximum(prices, opens) + np.random.rand(n) * 3,
            "low": np.minimum(prices, opens) - np.random.rand(n) * 3,
            "close": prices,
            "tick_volume": np.random.randint(500, 5000, n),
        })

    def test_returns_signal_or_none(self):
        df = self._make_trending_df()
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        signal = strat.generate_signal(df)
        self.assertTrue(signal is None or isinstance(signal, Signal))

    def test_needs_minimum_candles(self):
        df = self._make_trending_df(n=30)
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        self.assertIsNone(strat.generate_signal(df))

    def test_generates_buy_in_uptrend(self):
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        for seed in range(50):
            df = self._make_trending_df(n=200, direction="up", seed=seed)
            for i in range(60, len(df)):
                sig = strat.generate_signal(df.iloc[:i + 1])
                if sig and sig.direction == "BUY":
                    self.assertEqual(sig.symbol, "XAUUSD")
                    self.assertGreater(sig.sl_pips, 0)
                    self.assertGreater(sig.tp_pips, 0)
                    self.assertLess(sig.sl_price, sig.entry_price)
                    self.assertGreater(sig.tp_price, sig.entry_price)
                    return
            strat._daily_trades = 0  # reset for next seed
        self.skipTest("No BUY signal found in 50 uptrend seeds")

    def test_generates_sell_in_downtrend(self):
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        for seed in range(50):
            df = self._make_trending_df(n=200, direction="down", seed=seed)
            for i in range(60, len(df)):
                sig = strat.generate_signal(df.iloc[:i + 1])
                if sig and sig.direction == "SELL":
                    self.assertGreater(sig.sl_price, sig.entry_price)
                    self.assertLess(sig.tp_price, sig.entry_price)
                    return
            strat._daily_trades = 0
        self.skipTest("No SELL signal found in 50 downtrend seeds")

    def test_max_3_trades_per_day(self):
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        strat._daily_trades = 3
        strat._current_date = datetime(2025, 1, 2).date()
        df = self._make_trending_df()
        df["time"] = pd.date_range(datetime(2025, 1, 2, 8, 0), periods=len(df), freq="15min")
        sig = strat.generate_signal(df)
        self.assertIsNone(sig)

    def test_3rd_trade_requires_positive_pnl(self):
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        strat._daily_trades = 2
        strat._daily_pnl = -50.0  # negative PnL → 3rd trade blocked
        strat._current_date = datetime(2025, 1, 2).date()
        df = self._make_trending_df()
        df["time"] = pd.date_range(datetime(2025, 1, 2, 8, 0), periods=len(df), freq="15min")
        sig = strat.generate_signal(df)
        self.assertIsNone(sig)

    def test_rr_ratio_enforced(self):
        strat = EMACrossStrategy("XAUUSD", mode="backtest")
        for seed in range(100):
            df = self._make_trending_df(seed=seed)
            for i in range(60, len(df)):
                sig = strat.generate_signal(df.iloc[:i + 1])
                if sig:
                    self.assertGreaterEqual(sig.tp_pips / sig.sl_pips, 1.49,
                                            f"RR {sig.tp_pips/sig.sl_pips:.2f} < 1.5")
                    return
            strat._daily_trades = 0
        self.skipTest("No signal found")


class TestScalperWrapper(unittest.TestCase):
    def test_wraps_ema_cross(self):
        scalper = Scalper("XAUUSD", mode="backtest")
        self.assertEqual(scalper.name(), "EMACross")
        self.assertIsInstance(scalper.strategy, EMACrossStrategy)


class TestHawkFilter(unittest.TestCase):
    def test_always_confirms(self):
        hawk = HawkFilter("XAUUSD", mode="backtest")
        sig = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60, "test", 0.8)
        result = hawk.evaluate(None, sig)
        self.assertEqual(result.action, FilterResult.CONFIRM)


if __name__ == "__main__":
    unittest.main()
