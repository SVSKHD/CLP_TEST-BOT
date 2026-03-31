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
from confluence.zone_detector import detect_zones, get_active_zones, resample_to_h4
from confluence.trigger import detect_trigger
from confluence.momentum import check_momentum
from confluence.trend_filter import get_h1_trend


class TestConfluenceScorer(unittest.TestCase):
    def _make_df(self, n=500, base=2000.0, seed=42):
        np.random.seed(seed)
        prices = base + np.cumsum(np.random.randn(n) * 0.5)
        start = datetime(2025, 1, 2, 0, 0)
        opens = prices + np.random.randn(n) * 0.2
        return pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="15min"),
            "open": opens,
            "high": np.maximum(prices, opens) + np.random.rand(n) * 2,
            "low": np.minimum(prices, opens) - np.random.rand(n) * 2,
            "close": prices,
            "tick_volume": np.random.randint(100, 5000, n),
        })

    def test_scalper_returns_signal_or_none(self):
        df = self._make_df()
        scalper = Scalper("XAUUSD", mode="backtest")
        signal = scalper.generate_signal(df)
        self.assertTrue(signal is None or isinstance(signal, Signal))

    def test_scalper_needs_minimum_candles(self):
        df = self._make_df(n=30)
        scalper = Scalper("XAUUSD", mode="backtest")
        signal = scalper.generate_signal(df)
        self.assertIsNone(signal)

    def test_signal_has_correct_fields(self):
        scalper = Scalper("XAUUSD", mode="backtest")
        for seed in range(100):
            df = self._make_df(n=500, seed=seed)
            signal = scalper.generate_signal(df)
            if signal is not None:
                self.assertIn(signal.direction, ["BUY", "SELL"])
                self.assertEqual(signal.symbol, "XAUUSD")
                self.assertGreater(signal.sl_pips, 0)
                self.assertGreater(signal.tp_pips, 0)
                self.assertGreaterEqual(signal.confidence, 0.6)  # 3/5 = 0.6
                self.assertLessEqual(signal.confidence, 1.0)
                if signal.direction == "BUY":
                    self.assertLess(signal.sl_price, signal.entry_price)
                    self.assertGreater(signal.tp_price, signal.entry_price)
                else:
                    self.assertGreater(signal.sl_price, signal.entry_price)
                    self.assertLess(signal.tp_price, signal.entry_price)
                return
        self.skipTest("No signal in 100 seeds (acceptable)")


class TestZoneDetector(unittest.TestCase):
    def _make_h4_df(self, n=100, seed=42):
        np.random.seed(seed)
        prices = 2000 + np.cumsum(np.random.randn(n) * 2)
        opens = prices + np.random.randn(n) * 0.5
        return pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=n, freq="4h"),
            "open": opens,
            "high": np.maximum(prices, opens) + np.random.rand(n) * 3,
            "low": np.minimum(prices, opens) - np.random.rand(n) * 3,
            "close": prices,
            "tick_volume": np.random.randint(1000, 10000, n),
        })

    def test_detect_zones_returns_list(self):
        df = self._make_h4_df()
        zones = detect_zones(df)
        self.assertIsInstance(zones, list)

    def test_active_zones_capped(self):
        df = self._make_h4_df(n=200, seed=99)
        result = get_active_zones(df, df["close"].iloc[-1])
        self.assertLessEqual(len(result["demand"]), 4)
        self.assertLessEqual(len(result["supply"]), 4)

    def test_resample_h4(self):
        np.random.seed(42)
        n = 200
        prices = 2000 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
            "open": prices,
            "high": prices + 1,
            "low": prices - 1,
            "close": prices,
            "tick_volume": np.random.randint(100, 5000, n),
        })
        h4 = resample_to_h4(df)
        self.assertGreater(len(h4), 0)
        self.assertLess(len(h4), len(df))


class TestTrigger(unittest.TestCase):
    def test_trigger_returns_dict(self):
        np.random.seed(42)
        n = 100
        prices = 2000 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
            "open": prices - np.random.randn(n) * 0.2,
            "high": prices + np.random.rand(n) * 2,
            "low": prices - np.random.rand(n) * 2,
            "close": prices,
            "tick_volume": np.random.randint(500, 3000, n),
        })
        result = detect_trigger(df)
        self.assertIn("triggered", result)
        self.assertIn("direction", result)
        self.assertIn("pattern", result)

    def test_no_trigger_on_flat(self):
        n = 60
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
            "open":  [2000.0]*n,
            "high":  [2000.5]*n,
            "low":   [1999.5]*n,
            "close": [2000.1]*n,
            "tick_volume": [1000]*n,
        })
        result = detect_trigger(df)
        self.assertFalse(result["triggered"])


class TestMomentum(unittest.TestCase):
    def test_momentum_returns_dict(self):
        np.random.seed(42)
        n = 100
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
            "open": [2000]*n,
            "high": [2001]*n,
            "low":  [1999]*n,
            "close": 2000 + np.cumsum(np.random.randn(n) * 0.1),
            "tick_volume": np.random.randint(500, 2000, n),
        })
        result = check_momentum(df)
        self.assertIn("passed", result)
        self.assertIn("rsi", result)


class TestHawkFilter(unittest.TestCase):
    def test_hawk_always_confirms(self):
        hawk = HawkFilter("XAUUSD", mode="backtest")
        signal = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60, "test", 0.8)
        result = hawk.evaluate(None, signal)
        self.assertEqual(result.action, FilterResult.CONFIRM)


if __name__ == "__main__":
    unittest.main()
