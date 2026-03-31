import time
import logging
from functools import wraps

logger = logging.getLogger("astra.mt5_client")

_mt5 = None


def _get_mt5():
    global _mt5
    if _mt5 is None:
        import MetaTrader5 as mt5
        _mt5 = mt5
    return _mt5


def mt5_retry(max_retries=3, backoff=5):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    logger.warning(f"{func.__name__} attempt {attempt+1}/{max_retries} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(backoff)
            raise last_exc
        return wrapper
    return decorator


def initialize() -> bool:
    mt5 = _get_mt5()
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    info = mt5.account_info()
    if info is None:
        raise RuntimeError(f"MT5 not logged in: {mt5.last_error()}")
    logger.info(f"MT5 connected: account={info.login}, server={info.server}")
    return True


def shutdown():
    mt5 = _get_mt5()
    mt5.shutdown()
    logger.info("MT5 shutdown")


@mt5_retry()
def get_account_info() -> dict:
    mt5 = _get_mt5()
    info = mt5.account_info()
    if info is None:
        raise RuntimeError(f"Failed to get account info: {mt5.last_error()}")
    return {
        "login": info.login,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "profit": info.profit,
        "currency": info.currency,
    }


@mt5_retry()
def get_symbol_info(symbol: str) -> dict:
    mt5 = _get_mt5()
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol info failed for {symbol}: {mt5.last_error()}")
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return {
        "symbol": info.name,
        "bid": info.bid,
        "ask": info.ask,
        "spread": info.spread,
        "digits": info.digits,
        "point": info.point,
        "trade_tick_value": info.trade_tick_value,
        "trade_tick_size": info.trade_tick_size,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
    }


@mt5_retry()
def get_tick(symbol: str) -> dict:
    mt5 = _get_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Tick failed for {symbol}: {mt5.last_error()}")
    return {
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "time": tick.time,
    }


@mt5_retry()
def get_positions(symbol: str = None) -> list:
    mt5 = _get_mt5()
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    if positions is None:
        return []
    return [
        {
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "magic": p.magic,
            "time": p.time,
        }
        for p in positions
    ]


@mt5_retry()
def send_order(symbol: str, direction: str, lot: float, sl: float, tp: float,
               magic: int, comment: str = "ASTRA") -> dict:
    mt5 = _get_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Cannot get tick for order: {mt5.last_error()}")

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "BUY" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"Order send returned None: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Order failed: retcode={result.retcode}, comment={result.comment}")

    logger.info(f"Order placed: {direction} {lot} {symbol} @ {price}, SL={sl}, TP={tp}, ticket={result.order}")
    return {
        "ticket": result.order,
        "price": result.price,
        "volume": result.volume,
    }


@mt5_retry()
def close_position(ticket: int, symbol: str, direction: str, volume: float) -> dict:
    mt5 = _get_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Cannot get tick for close: {mt5.last_error()}")

    close_type = mt5.ORDER_TYPE_SELL if direction == "BUY" else mt5.ORDER_TYPE_BUY
    price = tick.bid if direction == "BUY" else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 0,
        "comment": "ASTRA_CLOSE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"Close returned None: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Close failed: retcode={result.retcode}, comment={result.comment}")

    logger.info(f"Position closed: ticket={ticket}, price={result.price}")
    return {"ticket": ticket, "close_price": result.price}


@mt5_retry()
def modify_sl(ticket: int, symbol: str, new_sl: float, tp: float) -> bool:
    mt5 = _get_mt5()
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": tp,
    }
    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"Modify SL returned None: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Modify SL failed: retcode={result.retcode}")
    logger.info(f"SL modified: ticket={ticket}, new_sl={new_sl}")
    return True


@mt5_retry()
def copy_rates(symbol: str, timeframe_str: str, start, count: int):
    mt5 = _get_mt5()
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe_str)
    if tf is None:
        raise ValueError(f"Unknown timeframe: {timeframe_str}")
    rates = mt5.copy_rates_from(symbol, tf, start, count)
    if rates is None:
        raise RuntimeError(f"copy_rates failed: {mt5.last_error()}")
    return rates


@mt5_retry()
def copy_rates_range(symbol: str, timeframe_str: str, date_from, date_to):
    mt5 = _get_mt5()
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe_str)
    if tf is None:
        raise ValueError(f"Unknown timeframe: {timeframe_str}")
    rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
    if rates is None:
        raise RuntimeError(f"copy_rates_range failed: {mt5.last_error()}")
    return rates


if __name__ == "__main__":
    initialize()
    print(get_account_info())
    for sym in ["XAUUSD", "XAUEUR", "XAUGBP"]:
        print(f"{sym}: {get_symbol_info(sym)}")
    shutdown()