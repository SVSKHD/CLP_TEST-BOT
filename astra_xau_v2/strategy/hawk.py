import logging
import pandas as pd

from strategy.base import BaseFilter, FilterResult, Signal

logger = logging.getLogger("astra.hawk")


class HawkFilter(BaseFilter):
    """Pass-through. All filtering is done by EMACrossStrategy."""

    def __init__(self, symbol: str, mode: str = "live"):
        super().__init__(symbol, mode)

    def name(self) -> str:
        return "Hawk"

    def evaluate(self, df: pd.DataFrame, signal: Signal) -> FilterResult:
        return FilterResult(FilterResult.CONFIRM, signal.reason)
