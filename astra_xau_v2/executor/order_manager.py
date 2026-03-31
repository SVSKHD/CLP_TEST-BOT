import logging
from config.settings import MAGIC_NUMBER

logger = logging.getLogger("astra.order_manager")

TRAIL_TRIGGER_PIPS = 50
TRAIL_DISTANCE_PIPS = 15
PIP_SIZE = 0.1


class OrderManager:
    def __init__(self, mode: str = "live"):
        self.mode = mode
        self.open_trades = {}

    def place_order(self, symbol: str, direction: str, lot: float,
                    sl: float, tp: float) -> dict:
        if self.mode == "live":
            return self._place_live(symbol, direction, lot, sl, tp)
        else:
            return {"ticket": None, "price": 0.0, "volume": lot, "mode": "backtest"}

    def _place_live(self, symbol, direction, lot, sl, tp) -> dict:
        from core.mt5_client import send_order
        result = send_order(symbol, direction, lot, sl, tp, MAGIC_NUMBER, "ASTRA")
        self.open_trades[result["ticket"]] = {
            "symbol": symbol,
            "direction": direction,
            "volume": result["volume"],
            "entry_price": result["price"],
            "sl": sl,
            "tp": tp,
            "ticket": result["ticket"],
            "trail_active": False,
        }
        return result

    def close_trade(self, ticket: int) -> dict:
        trade = self.open_trades.get(ticket)
        if not trade:
            logger.warning(f"Trade {ticket} not found in open_trades")
            return None

        if self.mode == "live":
            from core.mt5_client import close_position
            result = close_position(ticket, trade["symbol"], trade["direction"], trade["volume"])
            del self.open_trades[ticket]
            return result

        del self.open_trades[ticket]
        return {"ticket": ticket, "close_price": 0.0}

    def close_all_symbol(self, symbol: str) -> list:
        results = []
        tickets = [t for t, info in self.open_trades.items() if info["symbol"] == symbol]
        for ticket in tickets:
            result = self.close_trade(ticket)
            if result:
                results.append(result)
        logger.info(f"Closed {len(results)} trades for {symbol}")
        return results

    def close_all(self) -> list:
        results = []
        tickets = list(self.open_trades.keys())
        for ticket in tickets:
            result = self.close_trade(ticket)
            if result:
                results.append(result)
        logger.info(f"Closed all {len(results)} trades")
        return results

    def check_trailing_sl(self, ticket: int, current_price: float) -> bool:
        trade = self.open_trades.get(ticket)
        if not trade:
            return False

        entry = trade["entry_price"]
        direction = trade["direction"]

        if direction == "BUY":
            profit_pips = (current_price - entry) / PIP_SIZE
            if profit_pips >= TRAIL_TRIGGER_PIPS:
                new_sl = current_price - TRAIL_DISTANCE_PIPS * PIP_SIZE
                if new_sl > trade["sl"]:
                    return self._modify_sl(ticket, new_sl)
        else:
            profit_pips = (entry - current_price) / PIP_SIZE
            if profit_pips >= TRAIL_TRIGGER_PIPS:
                new_sl = current_price + TRAIL_DISTANCE_PIPS * PIP_SIZE
                if new_sl < trade["sl"]:
                    return self._modify_sl(ticket, new_sl)

        return False

    def _modify_sl(self, ticket: int, new_sl: float) -> bool:
        trade = self.open_trades[ticket]
        old_sl = trade["sl"]

        if self.mode == "live":
            from core.mt5_client import modify_sl
            modify_sl(ticket, trade["symbol"], new_sl, trade["tp"])

        trade["sl"] = new_sl
        trade["trail_active"] = True
        logger.info(f"Trail SL: ticket={ticket}, {old_sl:.2f} -> {new_sl:.2f}")
        return True

    def get_open_trade(self, symbol: str) -> dict:
        for ticket, trade in self.open_trades.items():
            if trade["symbol"] == symbol:
                return trade
        return None

    def has_open_trade(self, symbol: str) -> bool:
        return self.get_open_trade(symbol) is not None

    def get_all_open(self) -> dict:
        return dict(self.open_trades)


if __name__ == "__main__":
    om = OrderManager(mode="backtest")
    print(f"Open trades: {om.get_all_open()}")
    print(f"Has XAUUSD trade: {om.has_open_trade('XAUUSD')}")
