import logging
import pandas as pd
from core.market import calc_rsi

logger = logging.getLogger("astra.momentum")

RSI_LOW = 35
RSI_HIGH = 65
VOLUME_MULTIPLIER = 1.0
VOLUME_LOOKBACK = 20


def check_momentum(df: pd.DataFrame) -> dict:
    if len(df) < max(VOLUME_LOOKBACK, 14) + 1:
        return {"passed": False, "rsi": 0, "volume_ok": False, "reason": "insufficient data"}

    rsi = calc_rsi(df["close"], 14)
    rsi_val = rsi.iloc[-1]
    rsi_ok = RSI_LOW <= rsi_val <= RSI_HIGH

    vol_col = "tick_volume" if "tick_volume" in df.columns else None
    if vol_col:
        curr_vol = df[vol_col].iloc[-1]
        avg_vol = df[vol_col].iloc[-VOLUME_LOOKBACK:].mean()
        volume_ok = curr_vol > VOLUME_MULTIPLIER * avg_vol if avg_vol > 0 else True
    else:
        volume_ok = True
        curr_vol = 0
        avg_vol = 0

    passed = rsi_ok and volume_ok

    if not passed:
        reasons = []
        if not rsi_ok:
            reasons.append(f"RSI={rsi_val:.1f} outside {RSI_LOW}-{RSI_HIGH}")
        if not volume_ok:
            reasons.append(f"vol={curr_vol:.0f} < {VOLUME_MULTIPLIER}x avg={avg_vol:.0f}")
        reason = ", ".join(reasons)
    else:
        reason = f"RSI={rsi_val:.1f}, vol={curr_vol:.0f}/{avg_vol:.0f}"

    return {
        "passed": passed,
        "rsi": round(rsi_val, 1),
        "volume_ok": volume_ok,
        "reason": reason,
    }


if __name__ == "__main__":
    from backtest.data_loader import generate_synthetic_data
    df = generate_synthetic_data("XAUUSD", "2025-12-01", "2026-01-15", "M15", 2000)
    result = check_momentum(df)
    print(f"Momentum: {result}")
