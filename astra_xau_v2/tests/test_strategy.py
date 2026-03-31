import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

from strategy.scalper import Scalper
from strategy.hawk import HawkFilter
from strategy.momentum_price import MomentumPriceStrategy, MomentumSignal
from strategy.base import Signal, FilterResult


class TestMomentumPriceStrategy(unittest.TestCase):
    def _make_session_df(self, n=30, base=2000.0, move=0.0, seed=42):
        """Build M5 data starting at 07:00 GMT with optional directional move."""
        np.random.seed(seed)
        start = datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc)
        noise = np.random.randn(n) * 0.1
        trend = np.linspace(0, move, n)
        prices = base + trend + noise
        opens = prices - np.random.randn(n) * 0.05
        return pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="5min"),
            "open": opens,
            "high": np.maximum(prices, opens) + np.abs(np.random.randn(n) * 0.1),
            "low": np.minimum(prices, opens) - np.abs(np.random.randn(n) * 0.1),
            "close": prices,
            "tick_volume": np.random.randint(500, 5000, n),
        })

    def test_returns_signal_or_none(self):
        strat = MomentumPriceStrategy()
        df = self._make_session_df(move=3.0)
        sig = strat.get_signal(df)
        self.assertTrue(sig is None or isinstance(sig, MomentumSignal))

    def test_needs_minimum_bars(self):
        strat = MomentumPriceStrategy()
        df = self._make_session_df(n=10)
        self.assertIsNone(strat.get_signal(df))

    def test_blocks_outside_session(self):
        strat = MomentumPriceStrategy()
        # 15:00 GMT = outside London 07-10
        t = datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc)
        df = self._make_session_df(move=5.0)
        self.assertIsNone(strat.get_signal(df, current_time_utc=t))

    def test_blocks_small_move(self):
        strat = MomentumPriceStrategy()
        # Only $0.50 move in 30 candles — well below $2.00 trigger
        df = self._make_session_df(move=0.5)
        self.assertIsNone(strat.get_signal(df))

    def test_blocks_exhausted_leg(self):
        strat = MomentumPriceStrategy()
        # $8.00 move — above $6.00 exhaustion limit
        df = self._make_session_df(move=8.0)
        sig = strat.get_signal(df)
        # Either None (exhausted) or None (other gate) — should not pass
        # Note: may pass structure break but fail exhaustion
        if sig is not None:
            self.fail("Should have blocked exhausted leg")

    def test_buy_signal_has_correct_sl_tp(self):
        strat = MomentumPriceStrategy()
        for seed in range(200):
            df = self._make_session_df(n=30, move=3.0, seed=seed)
            sig = strat.get_signal(df)
            if sig and sig.direction == "BUY":
                self.assertAlmostEqual(sig.entry - sig.sl, 1.0, places=1)
                self.assertAlmostEqual(sig.tp - sig.entry, 2.0, places=1)
                return
        self.skipTest("No BUY signal in 200 seeds")

    def test_sell_signal_has_correct_sl_tp(self):
        strat = MomentumPriceStrategy()
        for seed in range(200):
            df = self._make_session_df(n=30, move=-3.0, seed=seed)
            sig = strat.get_signal(df)
            if sig and sig.direction == "SELL":
                self.assertAlmostEqual(sig.sl - sig.entry, 1.0, places=1)
                self.assertAlmostEqual(sig.entry - sig.tp, 2.0, places=1)
                return
        self.skipTest("No SELL signal in 200 seeds")


class TestScalperWrapper(unittest.TestCase):
    def test_wraps_momentum_price(self):
        scalper = Scalper("XAUUSD", mode="backtest")
        self.assertEqual(scalper.name(), "MomentumPrice")

    def test_returns_base_signal_type(self):
        scalper = Scalper("XAUUSD", mode="backtest")
        np.random.seed(42)
        n = 30
        start = datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc)
        prices = 2000.0 + np.linspace(0, 3.0, n) + np.random.randn(n) * 0.1
        opens = prices - np.random.randn(n) * 0.05
        df = pd.DataFrame({
            "time": pd.date_range(start, periods=n, freq="5min"),
            "open": opens,
            "high": np.maximum(prices, opens) + 0.1,
            "low": np.minimum(prices, opens) - 0.1,
            "close": prices,
            "tick_volume": np.random.randint(500, 5000, n),
        })
        sig = scalper.generate_signal(df)
        self.assertTrue(sig is None or isinstance(sig, Signal))


class TestHawkFilter(unittest.TestCase):
    def test_always_confirms(self):
        hawk = HawkFilter("XAUUSD", mode="backtest")
        sig = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60, "test", 0.8)
        result = hawk.evaluate(None, sig)
        self.assertEqual(result.action, FilterResult.CONFIRM)


if __name__ == "__main__":
    unittest.main()
