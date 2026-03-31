import time
import logging
from datetime import datetime

from strategy.scalper import Scalper
from strategy.hawk import HawkFilter
from strategy.base import FilterResult
from capital.allocator import calc_lot_size_live, calc_lot_size
from capital.profit_guard import ProfitGuard
from executor.order_manager import OrderManager, PIP_SIZE
from core.news_filter import is_news_blocked
from state.manager import update_state

logger = logging.getLogger("astra.engine")


class TradingEngine:
    def __init__(self, symbol: str, profit_guard: ProfitGuard,
                 order_manager: OrderManager, mode: str = "live"):
        self.symbol = symbol
        self.mode = mode
        self.profit_guard = profit_guard
        self.order_manager = order_manager
        self.scalper = Scalper(symbol, mode)
        self.hawk = HawkFilter(symbol, mode)
        self.running = False
        self._notifier = None
        self._mongo_logger = None
        self._chart_bridge = None

    def set_notifier(self, notifier):
        self._notifier = notifier

    def set_mongo_logger(self, mongo_logger):
        self._mongo_logger = mongo_logger

    def set_chart_bridge(self, bridge):
        self._chart_bridge = bridge

    def run_live(self, interval: float = 5.0):
        self.running = True
        logger.info(f"Engine started: {self.symbol} (live, {interval}s interval)")

        while self.running:
            try:
                self._tick_live()
            except Exception as e:
                logger.error(f"Engine tick error {self.symbol}: {e}")
            time.sleep(interval)

    def stop(self):
        self.running = False
        logger.info(f"Engine stopped: {self.symbol}")

    def _tick_live(self):
        # Check drawdown every tick
        try:
            from core.mt5_client import get_account_info
            account = get_account_info()
            dd_check = self.profit_guard.check_drawdown(account["equity"])
            if dd_check["breach"]:
                logger.critical(f"{self.symbol}: {dd_check['type']} — closing all trades")
                self.order_manager.close_all()
                self.stop()
                self._notify(f"CRITICAL: {dd_check['type']} at ${account['equity']:,.2f}")
                return
        except Exception as e:
            logger.debug(f"Drawdown check error: {e}")

        guard_check = self.profit_guard.can_trade(self.symbol)
        if not guard_check["allowed"]:
            logger.debug(f"{self.symbol} skip: {guard_check['reason']}")
            return

        news = is_news_blocked(self.symbol)
        if news["blocked"]:
            logger.info(f"{self.symbol} news blocked: {news['event']} until {news['unblock_time']}")
            return

        open_trade = self.order_manager.get_open_trade(self.symbol)

        if open_trade:
            self._manage_open_trade_live(open_trade)
        else:
            self._seek_entry_live()

    def _seek_entry_live(self):
        # Pre-flight: risk guard checks
        if self.profit_guard.is_emergency_stopped():
            logger.warning("BLOCKED: emergency stop active — manual reset required")
            return
        if self.profit_guard.is_halted():
            logger.info("BLOCKED: daily DD halt or loss cap reached — no new entries today")
            return
        if self.profit_guard.is_paused():
            logger.info("BLOCKED: consecutive loss pause active")
            return

        # Pre-flight: news skip day
        try:
            from scheduler.daily_init import is_news_skip_day
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            if is_news_skip_day(today_str):
                logger.info("BLOCKED: high-impact news day — no trading today")
                return
        except ImportError:
            pass  # scheduler not available in all contexts

        from core.market import fetch_candles_live
        df = fetch_candles_live(self.symbol, "M15", 200)

        signal = self.scalper.generate_signal(df)
        if signal is None:
            return

        hawk_result = self.hawk.evaluate(df, signal)
        if hawk_result.action != FilterResult.CONFIRM:
            logger.debug(f"{self.symbol} hawk {hawk_result.action}: {hawk_result.reason}")
            return

        lot = calc_lot_size_live(
            self.symbol,
            signal.sl_pips,
            [s for s in self.profit_guard.symbols if self.profit_guard.is_symbol_active(s)]
        )

        result = self.order_manager.place_order(
            self.symbol, signal.direction, lot, signal.sl_price, signal.tp_price
        )

        logger.info(f"Trade opened: {self.symbol} {signal.direction} {lot} @ {result['price']}")

        update_state(self.symbol, last_trade_id=result["ticket"])

        if self._chart_bridge:
            try:
                self._chart_bridge.draw_entry_line(self.symbol, result["price"], signal.direction, result["ticket"])
                self._chart_bridge.draw_sl_line(self.symbol, signal.sl_price, result["ticket"])
                self._chart_bridge.draw_tp_line(self.symbol, signal.tp_price, result["ticket"])
            except Exception as e:
                logger.debug(f"Chart bridge error: {e}")

        self._notify(f"OPEN: {signal.direction} {lot} {self.symbol} @ {result['price']:.2f}")

    def _manage_open_trade_live(self, trade: dict):
        from core.mt5_client import get_tick
        tick = get_tick(self.symbol)
        current = tick["bid"] if trade["direction"] == "BUY" else tick["ask"]
        self.order_manager.check_trailing_sl(trade["ticket"], current)

        floating = self._calc_floating(trade, current)
        self.profit_guard.update_floating(self.symbol, floating)

    def _calc_floating(self, trade: dict, current_price: float) -> float:
        if trade["direction"] == "BUY":
            pips = (current_price - trade["entry_price"]) / PIP_SIZE
        else:
            pips = (trade["entry_price"] - current_price) / PIP_SIZE
        return pips * trade["volume"] * 10

    def process_candle(self, df, candle_idx: int, equity: float, symbol_info: dict) -> dict:
        guard_check = self.profit_guard.can_trade(self.symbol)
        if not guard_check["allowed"]:
            return {"action": "SKIP", "reason": guard_check["reason"]}

        current = df.iloc[candle_idx]
        open_trade = self.order_manager.get_open_trade(self.symbol)

        if open_trade:
            return self._manage_open_trade_bt(open_trade, current)

        window = df.iloc[max(0, candle_idx - 199):candle_idx + 1].copy()
        if len(window) < 30:
            return {"action": "SKIP", "reason": "Insufficient candles"}

        signal = self.scalper.generate_signal(window)
        if signal is None:
            return {"action": "SKIP", "reason": "No signal"}

        hawk_result = self.hawk.evaluate(window, signal)
        if hawk_result.action != FilterResult.CONFIRM:
            return {"action": "SKIP", "reason": f"Hawk: {hawk_result.reason}"}

        active_symbols = [s for s in self.profit_guard.symbols if self.profit_guard.is_symbol_active(s)]
        lot = calc_lot_size(equity, symbol_info, signal.sl_pips, active_symbols)

        return {
            "action": "ENTRY",
            "signal": signal,
            "lot": lot,
            "candle_time": current["time"],
        }

    def _manage_open_trade_bt(self, trade: dict, candle) -> dict:
        if trade["direction"] == "BUY":
            if candle["low"] <= trade["sl"]:
                return {"action": "SL_HIT", "trade": trade, "exit_price": trade["sl"]}
            if candle["high"] >= trade["tp"]:
                return {"action": "TP_HIT", "trade": trade, "exit_price": trade["tp"]}
            current = candle["close"]
            self.order_manager.check_trailing_sl(trade["ticket"], current)
            if trade.get("trail_active") and candle["low"] <= trade["sl"]:
                return {"action": "TRAIL_EXIT", "trade": trade, "exit_price": trade["sl"]}
        else:
            if candle["high"] >= trade["sl"]:
                return {"action": "SL_HIT", "trade": trade, "exit_price": trade["sl"]}
            if candle["low"] <= trade["tp"]:
                return {"action": "TP_HIT", "trade": trade, "exit_price": trade["tp"]}
            current = candle["close"]
            self.order_manager.check_trailing_sl(trade["ticket"], current)
            if trade.get("trail_active") and candle["high"] >= trade["sl"]:
                return {"action": "TRAIL_EXIT", "trade": trade, "exit_price": trade["sl"]}

        return {"action": "HOLD"}

    def on_trade_closed(self, symbol: str, pnl: float, pips: float, trade_data: dict = None):
        self.profit_guard.update_realized(symbol, pnl, abs(pips))

        update_state(
            symbol,
            realized_pnl=self.profit_guard.realized_pnl[symbol],
            daily_pips=self.profit_guard.daily_pips[symbol],
            status=self.profit_guard.status[symbol],
        )

        if self._mongo_logger and trade_data:
            try:
                self._mongo_logger.log_trade(trade_data)
            except Exception as e:
                logger.debug(f"Mongo log error: {e}")

        if self._chart_bridge and trade_data:
            try:
                ticket = trade_data.get("ticket", 0)
                self._chart_bridge.draw_exit_marker(
                    symbol,
                    trade_data.get("exit_time", datetime.utcnow()),
                    trade_data.get("exit_price", 0),
                    ticket,
                    trade_data.get("result", ""),
                )
            except Exception as e:
                logger.debug(f"Chart bridge exit error: {e}")

        direction = "gain" if pnl > 0 else "loss"
        self._notify(
            f"CLOSE: {symbol} {direction} ${abs(pnl):.2f} ({abs(pips):.1f} pips) | "
            f"Day total: ${self.profit_guard.total_realized():.2f}"
        )

    def _notify(self, message: str):
        logger.info(message)
        if self._notifier:
            try:
                self._notifier.send(message)
            except Exception as e:
                logger.debug(f"Notify error: {e}")


if __name__ == "__main__":
    guard = ProfitGuard()
    om = OrderManager(mode="backtest")
    engine = TradingEngine("XAUUSD", guard, om, mode="backtest")
    print(f"Engine created: {engine.symbol}, mode={engine.mode}")
    print(f"Scalper: {engine.scalper.name()}, Hawk: {engine.hawk.name()}")
