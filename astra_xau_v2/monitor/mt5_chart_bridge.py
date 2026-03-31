import logging
from datetime import datetime

logger = logging.getLogger("astra.chart_bridge")

_mt5 = None


def _get_mt5():
    global _mt5
    if _mt5 is None:
        import MetaTrader5 as mt5
        _mt5 = mt5
    return _mt5


def _safe_call(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.debug(f"Chart bridge error in {func.__name__}: {e}")
            return None
    return wrapper


@_safe_call
def draw_entry_line(symbol: str, price: float, direction: str, ticket: int):
    mt5 = _get_mt5()
    name = f"astra_entry_{ticket}"
    color = 0x00FF00 if direction == "BUY" else 0x0000FF  # BGR: Green / Red

    request = {
        "action": 0,
        "chart_id": 0,
        "object_name": name,
        "object_type": mt5.OBJ_HLINE if hasattr(mt5, "OBJ_HLINE") else 1,
        "price": price,
        "color": color,
        "style": 0,
        "width": 2,
        "description": f"ASTRA {direction} #{ticket}",
    }

    try:
        mt5.chart_object_create(symbol, name, mt5.OBJ_HLINE, 0, datetime.utcnow(), price)
        logger.debug(f"Entry line drawn: {symbol} {direction} @ {price:.2f}")
    except AttributeError:
        _draw_via_script(symbol, name, "HLINE", price, color, f"ASTRA {direction} #{ticket}")


@_safe_call
def draw_sl_line(symbol: str, price: float, ticket: int):
    mt5 = _get_mt5()
    name = f"astra_sl_{ticket}"
    color = 0x004CFF  # BGR: OrangeRed

    try:
        mt5.chart_object_create(symbol, name, mt5.OBJ_HLINE, 0, datetime.utcnow(), price)
        logger.debug(f"SL line drawn: {symbol} @ {price:.2f}")
    except AttributeError:
        _draw_via_script(symbol, name, "HLINE", price, color, f"SL #{ticket}")


@_safe_call
def draw_tp_line(symbol: str, price: float, ticket: int):
    mt5 = _get_mt5()
    name = f"astra_tp_{ticket}"
    color = 0xFFBF00  # BGR: DeepSkyBlue

    try:
        mt5.chart_object_create(symbol, name, mt5.OBJ_HLINE, 0, datetime.utcnow(), price)
        logger.debug(f"TP line drawn: {symbol} @ {price:.2f}")
    except AttributeError:
        _draw_via_script(symbol, name, "HLINE", price, color, f"TP #{ticket}")


@_safe_call
def draw_trade_arrow(symbol: str, time: datetime, price: float, direction: str, ticket: int):
    mt5 = _get_mt5()
    name = f"astra_arrow_{ticket}"

    try:
        if direction == "BUY":
            obj_type = mt5.OBJ_ARROW_UP if hasattr(mt5, "OBJ_ARROW_UP") else 241
        else:
            obj_type = mt5.OBJ_ARROW_DOWN if hasattr(mt5, "OBJ_ARROW_DOWN") else 242

        mt5.chart_object_create(symbol, name, obj_type, 0, time, price)
        logger.debug(f"Trade arrow drawn: {symbol} {direction} @ {price:.2f}")
    except AttributeError:
        logger.debug(f"Arrow objects not supported in this MT5 build")


@_safe_call
def draw_exit_marker(symbol: str, time: datetime, price: float, ticket: int, result: str):
    mt5 = _get_mt5()
    name = f"astra_exit_{ticket}"
    color = 0x00FF00 if result == "WIN" else 0x0000FF  # Green / Red

    try:
        obj_type = mt5.OBJ_ARROW if hasattr(mt5, "OBJ_ARROW") else 22
        mt5.chart_object_create(symbol, name, obj_type, 0, time, price)
        logger.debug(f"Exit marker drawn: {symbol} {result} @ {price:.2f}")
    except AttributeError:
        logger.debug(f"Exit marker not supported")


@_safe_call
def clear_symbol_objects(symbol: str):
    mt5 = _get_mt5()

    try:
        total = mt5.chart_objects_total(0)
        removed = 0
        for i in range(total - 1, -1, -1):
            name = mt5.chart_object_name(0, i)
            if name and name.startswith("astra_"):
                mt5.chart_object_delete(0, name)
                removed += 1
        logger.info(f"Cleared {removed} ASTRA objects from {symbol} chart")
    except (AttributeError, TypeError):
        logger.debug(f"Chart object cleanup not available for {symbol}")


@_safe_call
def draw_daily_summary_label(symbol: str, pnl: float, pips: float, status: str = "ACTIVE"):
    mt5 = _get_mt5()
    name = f"astra_summary_{symbol}"
    text = f"ASTRA | PnL: ${pnl:.2f} | Pips: {pips:.1f} | {status}"

    try:
        obj_type = mt5.OBJ_LABEL if hasattr(mt5, "OBJ_LABEL") else 102
        mt5.chart_object_create(symbol, name, obj_type, 0, datetime.utcnow(), 0)
        logger.debug(f"Summary label updated: {symbol}")
    except AttributeError:
        logger.debug(f"Label objects not supported")


def _draw_via_script(symbol: str, name: str, obj_type: str, price: float,
                     color: int, description: str):
    logger.debug(f"Script fallback: {name} {obj_type} @ {price:.2f} ({description})")


if __name__ == "__main__":
    print("MT5 Chart Bridge — requires MT5 terminal to be running")
    print("Functions: draw_entry_line, draw_sl_line, draw_tp_line,")
    print("           draw_trade_arrow, draw_exit_marker,")
    print("           clear_symbol_objects, draw_daily_summary_label")
    print("All operations wrapped in try/except — failures never block trading")
