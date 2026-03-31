import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
from capital.allocator import calc_lot_size, calc_pip_value
from capital.profit_guard import ProfitGuard


class TestAllocator(unittest.TestCase):
    def setUp(self):
        self.symbol_info = {
            "trade_tick_value": 1.0,
            "trade_tick_size": 0.01,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
        }

    def test_pip_value_calculation(self):
        pv = calc_pip_value(self.symbol_info)
        self.assertAlmostEqual(pv, 1000.0, places=2)

    def test_lot_size_basic(self):
        lot = calc_lot_size(50000, self.symbol_info, 40, ["XAUUSD"])
        self.assertIsNotNone(lot)
        self.assertGreater(lot, 0)
        self.assertLessEqual(lot, 100.0)

    def test_lot_size_returns_none_below_min(self):
        lot = calc_lot_size(100, self.symbol_info, 40, ["XAUUSD"])
        self.assertIsNone(lot)

    def test_lot_size_respects_max(self):
        lot = calc_lot_size(100_000_000, self.symbol_info, 1, ["XAUUSD"])
        self.assertLessEqual(lot, 100.0)

    def test_lot_size_rounds_to_step(self):
        lot = calc_lot_size(500000, self.symbol_info, 40, ["XAUUSD"])
        self.assertIsNotNone(lot)
        # Verify lot is a multiple of volume_step (0.01) within float tolerance
        steps = lot / 0.01
        self.assertAlmostEqual(steps, round(steps), places=4)

    def test_lot_size_splits_across_symbols(self):
        info = {**self.symbol_info, "trade_tick_value": 0.01, "trade_tick_size": 0.01}
        lot_1 = calc_lot_size(50000, info, 40, ["XAUUSD"])
        lot_3 = calc_lot_size(50000, info, 40, ["XAUUSD", "XAUEUR", "XAUGBP"])
        self.assertGreater(lot_1, lot_3)


class TestProfitGuard(unittest.TestCase):
    def setUp(self):
        self.guard = ProfitGuard(["XAUUSD", "XAUEUR", "XAUGBP"])

    def test_initial_state(self):
        self.assertEqual(self.guard.global_status, "ACTIVE")
        for sym in self.guard.symbols:
            self.assertEqual(self.guard.status[sym], "ACTIVE")
            self.assertEqual(self.guard.realized_pnl[sym], 0.0)

    def test_update_realized(self):
        self.guard.update_realized("XAUUSD", 100, 20)
        self.assertEqual(self.guard.realized_pnl["XAUUSD"], 100)
        self.assertEqual(self.guard.daily_pips["XAUUSD"], 20)
        self.assertEqual(self.guard.trade_count["XAUUSD"], 1)

    def test_symbol_freeze_at_300(self):
        self.guard.update_realized("XAUUSD", 150, 30)
        self.assertEqual(self.guard.status["XAUUSD"], "ACTIVE")

        self.guard.update_realized("XAUUSD", 160, 32)
        self.assertEqual(self.guard.status["XAUUSD"], "FROZEN")
        self.assertFalse(self.guard.is_symbol_active("XAUUSD"))

    def test_other_symbols_unaffected_by_freeze(self):
        self.guard.update_realized("XAUUSD", 310, 62)
        self.assertEqual(self.guard.status["XAUUSD"], "FROZEN")
        self.assertEqual(self.guard.status["XAUEUR"], "ACTIVE")
        self.assertEqual(self.guard.status["XAUGBP"], "ACTIVE")

    def test_global_cap_at_3000(self):
        self.guard.update_realized("XAUUSD", 1000, 100)
        self.guard.update_realized("XAUEUR", 1000, 100)
        self.assertEqual(self.guard.global_status, "ACTIVE")

        self.guard.update_realized("XAUGBP", 1000, 100)
        self.assertEqual(self.guard.global_status, "GLOBAL_CAP")

        for sym in self.guard.symbols:
            self.assertEqual(self.guard.status[sym], "FROZEN")

    def test_can_trade_respects_freeze(self):
        self.guard.update_realized("XAUUSD", 310, 62)
        result = self.guard.can_trade("XAUUSD")
        self.assertFalse(result["allowed"])
        self.assertIn("frozen", result["reason"].lower())

    def test_can_trade_respects_global_cap(self):
        self.guard.update_realized("XAUUSD", 3000, 300)
        result = self.guard.can_trade("XAUEUR")
        self.assertFalse(result["allowed"])
        self.assertIn("cap", result["reason"].lower())

    def test_floor_alert(self):
        alert = self.guard.check_floor_alert()
        self.assertTrue(alert["alert"])
        self.assertEqual(alert["deficit"], 500)

        self.guard.update_realized("XAUUSD", 500, 100)
        alert = self.guard.check_floor_alert()
        self.assertFalse(alert["alert"])

    def test_reset(self):
        self.guard.update_realized("XAUUSD", 310, 62)
        self.guard.reset()
        self.assertEqual(self.guard.global_status, "ACTIVE")
        self.assertEqual(self.guard.realized_pnl["XAUUSD"], 0)
        self.assertEqual(self.guard.status["XAUUSD"], "ACTIVE")

    def test_freeze_callback(self):
        frozen_symbols = []
        self.guard.on_freeze(lambda sym, pnl: frozen_symbols.append(sym))
        self.guard.update_realized("XAUUSD", 310, 62)
        self.assertEqual(frozen_symbols, ["XAUUSD"])

    def test_global_cap_callback(self):
        cap_totals = []
        self.guard.on_global_cap(lambda total: cap_totals.append(total))
        self.guard.update_realized("XAUUSD", 1500, 150)
        self.guard.update_realized("XAUEUR", 1500, 150)
        self.assertEqual(len(cap_totals), 1)
        self.assertEqual(cap_totals[0], 3000)

    def test_total_pnl(self):
        self.guard.update_realized("XAUUSD", 100, 20)
        self.guard.update_floating("XAUEUR", 50)
        self.assertEqual(self.guard.total_realized(), 100)
        self.assertEqual(self.guard.total_floating(), 50)
        self.assertEqual(self.guard.total_pnl(), 150)


class TestDrawdownGuard(unittest.TestCase):
    def test_daily_dd_5pct_triggers_emergency(self):
        guard = ProfitGuard(["XAUUSD"], initial_equity=50000)
        guard.start_new_day(50000)
        # 5% of 50000 = 2500 → equity below 47500 triggers
        result = guard.check_drawdown(47400)
        self.assertTrue(result["breach"])
        self.assertEqual(result["type"], "EMERGENCY_STOP")
        self.assertEqual(guard.global_status, "EMERGENCY_STOP")
        self.assertFalse(guard.can_trade("XAUUSD")["allowed"])

    def test_daily_dd_within_limit(self):
        guard = ProfitGuard(["XAUUSD"], initial_equity=50000)
        guard.start_new_day(50000)
        result = guard.check_drawdown(47600)
        self.assertFalse(result["breach"])

    def test_total_dd_10pct_triggers_breach(self):
        guard = ProfitGuard(["XAUUSD"], initial_equity=50000)
        # Simulate loss spread over multiple days so daily DD doesn't fire first
        guard.start_new_day(46000)  # day starts at 46000 (already lost 4000)
        # 5% daily DD limit of 46000 = 43700
        # 10% total DD limit of 50000 = 45000
        # equity 44500 is within daily limit (> 43700) but below total limit (< 45000)
        result = guard.check_drawdown(44500)
        self.assertTrue(result["breach"])
        self.assertEqual(result["type"], "ACCOUNT_BREACH")
        self.assertEqual(guard.global_status, "ACCOUNT_BREACH")

    def test_emergency_callback_fires(self):
        guard = ProfitGuard(["XAUUSD"], initial_equity=50000)
        guard.start_new_day(50000)
        events = []
        guard.on_emergency_stop(lambda t, eq, dd: events.append((t, eq)))
        guard.check_drawdown(47000)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "EMERGENCY_STOP")


if __name__ == "__main__":
    unittest.main()
