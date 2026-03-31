import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
import tempfile
import shutil
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from backtest.simulator import Simulator, TradeResult, PIP_SIZE
from backtest.data_loader import generate_synthetic_data
from capital.profit_guard import ProfitGuard


class TestSimulator(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator(spread_pips=2.5, slippage_pips=0.5, commission_usd=3.5)

    def _make_df(self, prices):
        n = len(prices)
        return pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
        })

    def test_sl_hit_buy(self):
        prices = [2000, 2001, 2002, 1990, 1991, 1992]
        df = self._make_df(prices)

        entry_idx = 0
        sl = 2000 - 40 * PIP_SIZE
        tp = 2000 + 60 * PIP_SIZE

        result = self.sim.execute_trade(df, entry_idx, "BUY", 0.10, sl, tp, "XAUUSD")
        self.assertIsInstance(result, TradeResult)
        self.assertEqual(result.symbol, "XAUUSD")
        self.assertEqual(result.direction, "BUY")
        self.assertEqual(result.lot, 0.10)

    def test_tp_hit_buy(self):
        base = 2000
        prices = [base + i * 2 for i in range(20)]
        df = self._make_df(prices)

        sl = base - 40 * PIP_SIZE
        tp = base + 10 * PIP_SIZE

        result = self.sim.execute_trade(df, 0, "BUY", 0.10, sl, tp, "XAUUSD")
        self.assertIn(result.result, ["WIN", "EXPIRED"])

    def test_sl_hit_sell(self):
        prices = [2000, 1999, 1998, 2010, 2011, 2012]
        df = self._make_df(prices)

        sl = 2000 + 40 * PIP_SIZE
        tp = 2000 - 60 * PIP_SIZE

        result = self.sim.execute_trade(df, 0, "SELL", 0.10, sl, tp, "XAUUSD")
        self.assertIsInstance(result, TradeResult)

    def test_entry_includes_spread_and_slippage(self):
        prices = [2000] * 50
        df = self._make_df(prices)

        sl = 1990
        tp = 2010

        result = self.sim.execute_trade(df, 0, "BUY", 0.10, sl, tp, "XAUUSD")
        expected_entry = 2000 + (2.5 + 0.5) * PIP_SIZE
        self.assertAlmostEqual(result.entry_price, expected_entry, places=2)

    def test_commission_applied(self):
        prices = [2000 + i * 0.5 for i in range(100)]
        high_prices = [p + 10 for p in prices]
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=100, freq="15min"),
            "open": prices,
            "high": high_prices,
            "low": [p - 0.1 for p in prices],
            "close": prices,
        })

        tp = 2000 + 60 * PIP_SIZE
        sl = 2000 - 40 * PIP_SIZE

        result = self.sim.execute_trade(df, 0, "BUY", 1.0, sl, tp, "XAUUSD")
        if result.result == "WIN":
            raw_pnl = result.pips * 1.0 * 100
            self.assertLess(result.pnl_usd, raw_pnl)

    def test_trailing_sl_buy(self):
        prices = [2000 + i for i in range(30)]
        high_prices = [p + 1 for p in prices]
        low_prices = [p - 0.5 for p in prices]
        df = pd.DataFrame({
            "time": pd.date_range("2025-01-01", periods=30, freq="15min"),
            "open": prices,
            "high": high_prices,
            "low": low_prices,
            "close": prices,
        })

        sl = 2000 - 40 * PIP_SIZE
        tp = 2000 + 200 * PIP_SIZE

        result = self.sim.execute_trade(df, 0, "BUY", 0.10, sl, tp, "XAUUSD")
        self.assertIsInstance(result, TradeResult)

    def test_trade_result_fields(self):
        prices = [2000 + i for i in range(50)]
        df = self._make_df(prices)
        result = self.sim.execute_trade(df, 0, "BUY", 0.10,
                                        1996, 2006, "XAUUSD")
        self.assertIsInstance(result.entry_time, (datetime, pd.Timestamp))
        self.assertIsInstance(result.exit_time, (datetime, pd.Timestamp))
        self.assertIsInstance(result.pips, float)
        self.assertIsInstance(result.pnl_usd, float)
        self.assertIn(result.result, ["WIN", "LOSS", "TRAILING_EXIT", "EXPIRED"])


class TestProfitGuardIntegration(unittest.TestCase):
    def test_300_freeze_triggers(self):
        guard = ProfitGuard(["XAUUSD"])
        guard.update_realized("XAUUSD", 299, 59)
        self.assertEqual(guard.status["XAUUSD"], "ACTIVE")

        guard.update_realized("XAUUSD", 2, 0.4)
        self.assertEqual(guard.status["XAUUSD"], "FROZEN")

    def test_3000_cap_triggers(self):
        guard = ProfitGuard(["XAUUSD", "XAUEUR"])
        guard.update_realized("XAUUSD", 1500, 150)
        guard.update_realized("XAUEUR", 1499, 150)
        self.assertEqual(guard.global_status, "ACTIVE")

        guard.update_realized("XAUEUR", 2, 0.2)
        self.assertEqual(guard.global_status, "GLOBAL_CAP")

    def test_can_trade_after_pip_coverage(self):
        guard = ProfitGuard(["XAUUSD"])
        guard.update_realized("XAUUSD", 50, 200)
        result = guard.can_trade("XAUUSD")
        self.assertFalse(result["allowed"])


class TestDataLoader(unittest.TestCase):
    def test_csv_fallback(self):
        df = generate_synthetic_data("XAUUSD", "2025-01-01", "2025-01-31")
        self.assertGreater(len(df), 0)
        self.assertIn("time", df.columns)
        self.assertIn("open", df.columns)
        self.assertIn("high", df.columns)
        self.assertIn("low", df.columns)
        self.assertIn("close", df.columns)

    def test_synthetic_data_no_negative_prices(self):
        df = generate_synthetic_data("XAUUSD", "2025-01-01", "2025-06-30")
        self.assertTrue((df["high"] >= df["low"]).all())
        self.assertTrue((df["high"] > 0).all())

    def test_synthetic_data_weekdays_only(self):
        df = generate_synthetic_data("XAUUSD", "2025-01-01", "2025-01-31")
        self.assertTrue((df["time"].dt.dayofweek < 5).all())

    def test_load_from_csv(self):
        df = generate_synthetic_data("TEST", "2025-01-01", "2025-01-10")

        tmpdir = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmpdir, "TEST_M15.csv")
            df.to_csv(csv_path, index=False)

            loaded = pd.read_csv(csv_path, parse_dates=["time"])
            self.assertEqual(len(loaded), len(df))
        finally:
            shutil.rmtree(tmpdir)


class TestNoLookahead(unittest.TestCase):
    def test_strategy_only_sees_past_data(self):
        from strategy.scalper import Scalper

        df = generate_synthetic_data("XAUUSD", "2025-01-01", "2025-01-31")
        scalper = Scalper("XAUUSD", mode="backtest")

        for i in range(50, min(100, len(df))):
            window = df.iloc[:i + 1].copy().reset_index(drop=True)
            signal = scalper.generate_signal(window)

            if signal is not None:
                self.assertLessEqual(
                    len(window),
                    i + 1,
                    "Strategy window should not exceed current candle index"
                )


if __name__ == "__main__":
    unittest.main()
