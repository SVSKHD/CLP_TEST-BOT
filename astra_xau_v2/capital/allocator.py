import math
import logging

from config.settings import RISK_PER_TRADE_PCT, SYMBOLS

logger = logging.getLogger("astra.allocator")


def calc_pip_value(symbol_info: dict) -> float:
    tick_value = symbol_info["trade_tick_value"]
    tick_size = symbol_info["trade_tick_size"]
    pip_value_per_lot = (tick_value / tick_size) * 10
    return pip_value_per_lot


def calc_lot_size(
    equity: float,
    symbol_info: dict,
    sl_pips: float,
    active_symbols: list = None,
    risk_pct: float = None,
) -> float:
    if active_symbols is None:
        active_symbols = SYMBOLS
    if risk_pct is None:
        risk_pct = RISK_PER_TRADE_PCT

    n_symbols = max(len(active_symbols), 1)
    risk_amount = equity * risk_pct / n_symbols
    pip_value = calc_pip_value(symbol_info)

    if pip_value <= 0 or sl_pips <= 0:
        logger.error(f"Invalid pip_value={pip_value} or sl_pips={sl_pips}")
        return symbol_info.get("volume_min", 0.01)

    raw_lot = risk_amount / (sl_pips * pip_value)

    vol_min = symbol_info.get("volume_min", 0.01)
    vol_max = symbol_info.get("volume_max", 100.0)
    vol_step = symbol_info.get("volume_step", 0.01)

    if raw_lot < vol_min:
        actual_risk = vol_min * sl_pips * pip_value
        logger.warning(
            f"SKIPPED: raw lot {raw_lot:.6f} below min {vol_min}, "
            f"actual risk would be {actual_risk:.2f} vs intended {risk_amount:.2f}"
        )
        return None

    lot = min(raw_lot, vol_max)
    lot = math.floor(lot / vol_step) * vol_step
    lot = round(lot, 8)

    logger.info(
        f"Lot calc: equity={equity:.2f}, risk_amt={risk_amount:.2f}, "
        f"sl_pips={sl_pips}, pip_val={pip_value:.2f}, raw={raw_lot:.4f}, final={lot}"
    )
    return lot


def calc_lot_size_live(symbol: str, sl_pips: float, active_symbols: list = None) -> float:
    from core.mt5_client import get_account_info, get_symbol_info
    account = get_account_info()
    sym_info = get_symbol_info(symbol)
    return calc_lot_size(account["equity"], sym_info, sl_pips, active_symbols)


if __name__ == "__main__":
    mock_info = {
        "trade_tick_value": 1.0,
        "trade_tick_size": 0.01,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
    }
    lot = calc_lot_size(50000, mock_info, 40, ["XAUUSD", "XAUEUR", "XAUGBP"])
    print(f"Lot size for $50k equity, 40 pip SL, 3 symbols: {lot}")
    pip_val = calc_pip_value(mock_info)
    print(f"Pip value per lot: ${pip_val:.2f}")
    print(f"Risk per trade: ${50000 * 0.01 / 3:.2f}")
    print(f"Expected P&L at TP (60 pips): ${lot * 60 * pip_val:.2f}")
