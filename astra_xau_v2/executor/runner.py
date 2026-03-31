import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from config.settings import SYMBOLS
from capital.profit_guard import ProfitGuard
from executor.order_manager import OrderManager
from executor.engine import TradingEngine

logger = logging.getLogger("astra.runner")


class Runner:
    def __init__(self, symbols: list = None, mode: str = "live"):
        self.symbols = symbols or SYMBOLS
        self.mode = mode
        self.profit_guard = ProfitGuard(self.symbols)
        self.engines = {}
        self._threads = {}
        self._running = False

        self.profit_guard.on_freeze(self._on_symbol_freeze)
        self.profit_guard.on_global_cap(self._on_global_cap)

        for symbol in self.symbols:
            om = OrderManager(mode=mode)
            engine = TradingEngine(symbol, self.profit_guard, om, mode)
            self.engines[symbol] = engine

    def set_notifier(self, notifier):
        for engine in self.engines.values():
            engine.set_notifier(notifier)

    def set_mongo_logger(self, mongo_logger):
        for engine in self.engines.values():
            engine.set_mongo_logger(mongo_logger)

    def set_chart_bridge(self, bridge):
        for engine in self.engines.values():
            engine.set_chart_bridge(bridge)

    def start(self, interval: float = 5.0):
        self._running = True
        logger.info(f"Runner starting: {self.symbols}, mode={self.mode}")

        for symbol, engine in self.engines.items():
            t = threading.Thread(
                target=engine.run_live,
                args=(interval,),
                name=f"engine-{symbol}",
                daemon=True,
            )
            self._threads[symbol] = t
            t.start()
            logger.info(f"Engine thread started: {symbol}")

    def stop(self):
        self._running = False
        for symbol, engine in self.engines.items():
            engine.stop()
        for symbol, thread in self._threads.items():
            thread.join(timeout=10)
        logger.info("All engines stopped")

    def _on_symbol_freeze(self, symbol: str, pnl: float):
        logger.info(f"Runner: freezing {symbol}, closing trades")
        engine = self.engines.get(symbol)
        if engine:
            engine.order_manager.close_all_symbol(symbol)

    def _on_global_cap(self, total: float):
        logger.info(f"Runner: GLOBAL CAP ${total:.2f}, closing ALL trades")
        for engine in self.engines.values():
            engine.order_manager.close_all()
            engine.stop()

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "profit_guard": self.profit_guard.get_summary(),
            "threads": {
                sym: t.is_alive() for sym, t in self._threads.items()
            },
        }


if __name__ == "__main__":
    runner = Runner(mode="backtest")
    print(f"Runner created: {runner.symbols}")
    print(f"Status: {runner.get_status()}")
