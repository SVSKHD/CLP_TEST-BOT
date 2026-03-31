import logging
import json
import os
from dataclasses import dataclass, asdict
from typing import List, Optional

import pandas as pd
import numpy as np

from core.market import calc_atr

logger = logging.getLogger("astra.zone_detector")

PIP_SIZE = 0.1
ZONE_INVALIDATION_PIPS = 5
MAX_ZONES_PER_DIRECTION = 8
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "state")


@dataclass
class Zone:
    zone_type: str          # "demand" or "supply"
    low: float
    high: float
    strength: float         # body_size / atr ratio
    candle_index: int
    invalidated: bool = False


def detect_zones(df: pd.DataFrame, atr_period: int = 14) -> List[Zone]:
    if len(df) < atr_period + 2:
        return []

    atr = calc_atr(df, atr_period)
    zones = []

    for i in range(atr_period + 1, len(df) - 1):
        body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        atr_val = atr.iloc[i]

        if atr_val <= 0:
            continue

        body_ratio = body / atr_val

        if body_ratio < 0.5:
            continue

        next_close = df["close"].iloc[i + 1]
        curr_close = df["close"].iloc[i]
        curr_open = df["open"].iloc[i]

        # Demand zone: big bullish candle, next candle continues up
        if curr_close > curr_open and next_close > curr_close:
            zone_low = df["low"].iloc[i]
            zone_high = curr_open + atr_val * 0.2  # extend zone into the body
            zones.append(Zone(
                zone_type="demand",
                low=zone_low,
                high=max(zone_high, zone_low + body * 0.6),
                strength=body_ratio,
                candle_index=i,
            ))

        # Supply zone: big bearish candle, next candle continues down
        elif curr_close < curr_open and next_close < curr_close:
            zone_high = df["high"].iloc[i]
            zone_low = curr_open - atr_val * 0.2
            zones.append(Zone(
                zone_type="supply",
                low=min(zone_low, zone_high - body * 0.6),
                high=zone_high,
                strength=body_ratio,
                candle_index=i,
            ))

    return zones


def get_active_zones(df: pd.DataFrame, current_price: float) -> dict:
    raw_zones = detect_zones(df)

    # Invalidate zones price has closed through
    for z in raw_zones:
        if z.zone_type == "demand" and current_price < z.low - ZONE_INVALIDATION_PIPS * PIP_SIZE:
            z.invalidated = True
        elif z.zone_type == "supply" and current_price > z.high + ZONE_INVALIDATION_PIPS * PIP_SIZE:
            z.invalidated = True

    active = [z for z in raw_zones if not z.invalidated]

    # Keep top N strongest per direction, prefer recent
    demand = sorted(
        [z for z in active if z.zone_type == "demand"],
        key=lambda z: (z.strength, z.candle_index),
        reverse=True,
    )[:MAX_ZONES_PER_DIRECTION]

    supply = sorted(
        [z for z in active if z.zone_type == "supply"],
        key=lambda z: (z.strength, z.candle_index),
        reverse=True,
    )[:MAX_ZONES_PER_DIRECTION]

    return {"demand": demand, "supply": supply}


def price_in_zone(price: float, zones: List[Zone]) -> Optional[Zone]:
    for z in zones:
        if z.low <= price <= z.high:
            return z
    return None


def nearest_zone_above(price: float, zones: List[Zone]) -> Optional[Zone]:
    above = [z for z in zones if z.low > price]
    if above:
        return min(above, key=lambda z: z.low)
    return None


def nearest_zone_below(price: float, zones: List[Zone]) -> Optional[Zone]:
    below = [z for z in zones if z.high < price]
    if below:
        return max(below, key=lambda z: z.high)
    return None


def resample_to_h4(df_m15: pd.DataFrame) -> pd.DataFrame:
    df = df_m15.copy()
    df = df.set_index("time")
    h4 = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "tick_volume": "sum",
    }).dropna()
    return h4.reset_index()


if __name__ == "__main__":
    from backtest.data_loader import generate_synthetic_data
    df = generate_synthetic_data("XAUUSD", "2025-12-01", "2026-01-15", "M15", 2000)
    h4 = resample_to_h4(df)
    print(f"H4 candles: {len(h4)}")
    zones = get_active_zones(h4, h4["close"].iloc[-1])
    print(f"Demand zones: {len(zones['demand'])}")
    print(f"Supply zones: {len(zones['supply'])}")
    for z in zones["demand"][:2]:
        print(f"  Demand: {z.low:.2f} - {z.high:.2f} (str={z.strength:.2f})")
    for z in zones["supply"][:2]:
        print(f"  Supply: {z.low:.2f} - {z.high:.2f} (str={z.strength:.2f})")
