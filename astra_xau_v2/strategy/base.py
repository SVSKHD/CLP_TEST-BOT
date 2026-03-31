from abc import ABC, abstractmethod
import pandas as pd


class Signal:
    def __init__(self, direction: str, symbol: str, entry_price: float,
                 sl_price: float, tp_price: float, sl_pips: float, tp_pips: float,
                 reason: str = "", confidence: float = 0.0):
        self.direction = direction
        self.symbol = symbol
        self.entry_price = entry_price
        self.sl_price = sl_price
        self.tp_price = tp_price
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips
        self.reason = reason
        self.confidence = confidence

    def __repr__(self):
        return (f"Signal({self.direction} {self.symbol} @ {self.entry_price:.2f}, "
                f"SL={self.sl_price:.2f} [{self.sl_pips}p], TP={self.tp_price:.2f} [{self.tp_pips}p], "
                f"conf={self.confidence:.2f})")


class BaseStrategy(ABC):
    def __init__(self, symbol: str, mode: str = "live"):
        self.symbol = symbol
        self.mode = mode

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        pass

    @abstractmethod
    def name(self) -> str:
        pass


class FilterResult:
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    MONITOR = "MONITOR"

    def __init__(self, action: str, reason: str = ""):
        self.action = action
        self.reason = reason

    def __repr__(self):
        return f"FilterResult({self.action}: {self.reason})"


class BaseFilter(ABC):
    def __init__(self, symbol: str, mode: str = "live"):
        self.symbol = symbol
        self.mode = mode

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, signal: Signal) -> FilterResult:
        pass

    @abstractmethod
    def name(self) -> str:
        pass
