import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
import numpy as np
import pandas as pd
from datetime import datetime

from strategy.scalper import Scalper
from strategy.hawk import HawkFilter
from strategy.base import Signal, FilterResult


class TestScalper(unittest.TestCase):
    def _make_df(self, n=200, base=2000.0, trend=0.0, seed=42):
        np.random.seed(seed)
        t = np.linspace(0, trend, n)
        prices = base + t + np.cumsum(np.random.randn(n) * 0.3)
        start = datetime(2025, 1, 2, 8, 0)
        return pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="15min"),
            "open": prices,
            "high": prices + np.random.rand(n) * 2,
            "low": prices - np.random.rand(n) * 2,
            "close": prices + np.random.randn(n) * 0.2,
            "tick_volume": np.random.randint(100, 5000, n),
            "spread": np.random.uniform(0.15, 0.35, n),
        })

    def test_scalper_returns_signal_or_none(self):
        df = self._make_df()
        scalper = Scalper("XAUUSD", mode="backtest")
        signal = scalper.generate_signal(df)
        self.assertTrue(signal is None or isinstance(signal, Signal))

    def test_scalper_needs_minimum_candles(self):
        df = self._make_df(n=10)
        scalper = Scalper("XAUUSD", mode="backtest")
        signal = scalper.generate_signal(df)
        self.assertIsNone(signal)

    def test_signal_has_correct_fields(self):
        scalper = Scalper("XAUUSD", mode="backtest")
        for seed in range(50):
            df = self._make_df(seed=seed)
            signal = scalper.generate_signal(df)
            if signal is not None:
                self.assertIn(signal.direction, ["BUY", "SELL"])
                self.assertEqual(signal.symbol, "XAUUSD")
                self.assertGreater(signal.sl_pips, 0)
                self.assertGreater(signal.tp_pips, 0)
                self.assertGreater(signal.confidence, 0)
                self.assertLessEqual(signal.confidence, 1.0)

                if signal.direction == "BUY":
                    self.assertLess(signal.sl_price, signal.entry_price)
                    self.assertGreater(signal.tp_price, signal.entry_price)
                else:
                    self.assertGreater(signal.sl_price, signal.entry_price)
                    self.assertLess(signal.tp_price, signal.entry_price)
                return
        self.skipTest("No signal generated in 50 attempts (acceptable for random data)")


class TestHawkFilter(unittest.TestCase):
    def _make_df(self, n=300, trend_up=True, seed=42):
        np.random.seed(seed)
        base = 2000.0
        trend = np.linspace(0, 15 if trend_up else -15, n)
        noise = np.cumsum(np.random.randn(n) * 0.3)
        prices = base + trend + noise

        start = datetime(2025, 1, 2, 8, 0)
        return pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="15min"),
            "open": prices,
            "high": prices + np.random.rand(n) * 2,
            "low": prices - np.random.rand(n) * 2,
            "close": prices + np.random.randn(n) * 0.2,
            "tick_volume": np.random.randint(100, 5000, n),
        })

    def test_hawk_returns_filter_result(self):
        df = self._make_df()
        hawk = HawkFilter("XAUUSD", mode="backtest")
        signal = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60)
        result = hawk.evaluate(df, signal)
        self.assertIsInstance(result, FilterResult)
        self.assertIn(result.action, [FilterResult.CONFIRM, FilterResult.REJECT, FilterResult.MONITOR])

    def test_hawk_rejects_outside_session_live(self):
        np.random.seed(42)
        n = 300
        prices = 2000 + np.cumsum(np.random.randn(n) * 0.3)
        start = datetime(2025, 1, 2, 20, 0)
        df = pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="15min"),
            "open": prices,
            "high": prices + np.random.rand(n) * 2,
            "low": prices - np.random.rand(n) * 2,
            "close": prices + np.random.randn(n) * 0.2,
            "tick_volume": np.random.randint(100, 5000, n),
        })

        hawk = HawkFilter("XAUUSD", mode="live")
        signal = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60)
        result = hawk.evaluate(df, signal)
        self.assertEqual(result.action, FilterResult.REJECT)

    def test_hawk_skips_session_filter_in_backtest(self):
        np.random.seed(42)
        n = 300
        prices = 2000 + np.cumsum(np.random.randn(n) * 0.3)
        start = datetime(2025, 1, 2, 20, 0)
        df = pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="15min"),
            "open": prices,
            "high": prices + np.random.rand(n) * 2,
            "low": prices - np.random.rand(n) * 2,
            "close": prices + np.random.randn(n) * 0.2,
            "tick_volume": np.random.randint(100, 5000, n),
        })

        hawk = HawkFilter("XAUUSD", mode="backtest")
        signal = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60)
        result = hawk.evaluate(df, signal)
        self.assertNotEqual(result.action, FilterResult.REJECT,
                            "Backtest mode should not reject on session filter")

    def test_hawk_insufficient_data(self):
        df = self._make_df(n=20)
        hawk = HawkFilter("XAUUSD", mode="backtest")
        signal = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60)
        result = hawk.evaluate(df, signal)
        self.assertEqual(result.action, FilterResult.MONITOR)


if __name__ == "__main__":
    unittest.main()
