import logging
import pandas as pd

from strategy.base import BaseFilter, FilterResult, Signal

logger = logging.getLogger("astra.hawk")


class HawkFilter(BaseFilter):
    """Pass-through filter. All filtering is done by the ConfluenceScorer
    in the scalper. Hawk confirms any signal that scored >= 4/5."""

    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)

    def name(self) -> str:
        return "Hawk"

    def evaluate(self, df: pd.DataFrame, signal: Signal) -> FilterResult:
        # Confluence scorer already validated all 5 layers.
        # Signal only reaches hawk if score >= 4/5.
        logger.info(
            f"Hawk {self.symbol}: CONFIRM {signal.direction} "
            f"(conf={signal.confidence:.2f}, {signal.reason})"
        )
        return FilterResult(FilterResult.CONFIRM, signal.reason)


if __name__ == "__main__":
    from strategy.base import Signal

    hawk = HawkFilter("XAUUSD", mode="backtest")
    sig = Signal("BUY", "XAUUSD", 2010, 2006, 2016, 40, 60, "test", 0.8)
    result = hawk.evaluate(None, sig)
    print(f"Hawk result: {result}")
