import json
import os
import logging
import tempfile
from datetime import datetime

logger = logging.getLogger("astra.state")

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "state")


def _state_path(symbol: str) -> str:
    return os.path.join(STATE_DIR, f"{symbol}.json")


def _default_state(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "status": "ACTIVE",
        "realized_pnl": 0.0,
        "floating_pnl": 0.0,
        "daily_pips": 0.0,
        "trade_count": 0,
        "last_trade_id": None,
        "last_updated": datetime.utcnow().isoformat(),
    }


def load_state(symbol: str) -> dict:
    path = _state_path(symbol)
    if not os.path.exists(path):
        return _default_state(symbol)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load state for {symbol}: {e}")
        return _default_state(symbol)


def save_state(symbol: str, state: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(symbol)
    state["last_updated"] = datetime.utcnow().isoformat()

    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error(f"Failed to save state for {symbol}: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def update_state(symbol: str, **kwargs):
    state = load_state(symbol)
    state.update(kwargs)
    save_state(symbol, state)


def reset_state(symbol: str):
    state = _default_state(symbol)
    save_state(symbol, state)
    logger.info(f"State reset: {symbol}")


def reset_all(symbols: list):
    for s in symbols:
        reset_state(s)


def load_all(symbols: list) -> dict:
    return {s: load_state(s) for s in symbols}


if __name__ == "__main__":
    from config.settings import SYMBOLS

    for sym in SYMBOLS:
        reset_state(sym)
        state = load_state(sym)
        print(f"{sym}: {json.dumps(state, indent=2)}")

    update_state("XAUUSD", realized_pnl=150.0, daily_pips=30.0, last_trade_id=12345)
    print(f"\nUpdated XAUUSD: {json.dumps(load_state('XAUUSD'), indent=2)}")
