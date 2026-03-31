SYMBOL_CONFIG = {
    "XAUUSD": {
        "enabled": True,
        "base": "XAU",
        "quote": "USD",
        "digits": 2,
        "pip_digits": 1,
        "session_london": ("07:00", "12:00"),
        "session_newyork": ("13:00", "18:30"),
        "typical_spread_pips": 2.0,
        "max_spread_pips": 6.0,
    },
    "XAUEUR": {
        "enabled": False,
        "reason": "PF 0.92 — net negative in backtest",
        "base": "XAU",
        "quote": "EUR",
        "digits": 2,
        "pip_digits": 1,
        "session_london": ("07:00", "12:00"),
        "session_newyork": ("13:00", "18:30"),
        "typical_spread_pips": 3.0,
        "max_spread_pips": 9.0,
    },
    "XAUGBP": {
        "enabled": False,
        "reason": "suspended",
        "base": "XAU",
        "quote": "GBP",
        "digits": 2,
        "pip_digits": 1,
        "session_london": ("07:00", "12:00"),
        "session_newyork": ("13:00", "18:30"),
        "typical_spread_pips": 3.5,
        "max_spread_pips": 10.5,
    },
}


def get_symbol_config(symbol: str) -> dict:
    if symbol not in SYMBOL_CONFIG:
        raise ValueError(f"Unknown symbol: {symbol}")
    return SYMBOL_CONFIG[symbol]


def get_max_spread(symbol: str) -> float:
    return SYMBOL_CONFIG[symbol]["max_spread_pips"]


def get_sessions(symbol: str) -> dict:
    cfg = SYMBOL_CONFIG[symbol]
    return {
        "london": cfg["session_london"],
        "newyork": cfg["session_newyork"],
    }


if __name__ == "__main__":
    for sym, cfg in SYMBOL_CONFIG.items():
        print(f"{sym}: typical_spread={cfg['typical_spread_pips']} pips, "
              f"max_spread={cfg['max_spread_pips']} pips")
