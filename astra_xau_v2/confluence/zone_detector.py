import logging
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import numpy as np

from core.market import calc_atr

logger = logging.getLogger("astra.zone_detector")

PIP_SIZE = 0.1
ZONE_INVALIDATION_PIPS = 5
MAX_ZONES_PER_DIRECTION = 8
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "state")

# Grade thresholds
A_GRADE_BODY_ATR = 2.0
B_GRADE_BODY_ATR = 1.2
A_GRADE_MAX_AGE = 20       # H4 candles
B_GRADE_MAX_AGE = 40
A_GRADE_MAX_TESTS = 0      # fresh only
B_GRADE_MAX_TESTS = 1
DWELL_LIMIT = 3            # max consecutive H4 candles inside zone → C-grade
APPROACH_PIPS = 50          # A-grade requires price came from 50+ pips away


@dataclass
class Zone:
    zone_type: str          # "demand" or "supply"
    low: float
    high: float
    strength: float         # body_size / atr ratio
    candle_index: int
    total_candles: int      # total H4 candles in the dataset (for age calc)
    tests: int = 0          # how many times price re-entered this zone
    dwell: int = 0          # consecutive H4 candles price spent inside zone
    grade: str = "C"        # A, B, or C
    invalidated: bool = False
    score_contribution: float = 0.0


def detect_zones(df: pd.DataFrame, atr_period: int = 14) -> List[Zone]:
    if len(df) < atr_period + 2:
        return []

    atr = calc_atr(df, atr_period)
    zones = []
    n = len(df)

    for i in range(atr_period + 1, n - 1):
        body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        atr_val = atr.iloc[i]

        if atr_val <= 0:
            continue

        body_ratio = body / atr_val

        # Minimum threshold for any zone (even C-grade needs 0.5)
        if body_ratio < 0.5:
            continue

        next_close = df["close"].iloc[i + 1]
        curr_close = df["close"].iloc[i]
        curr_open = df["open"].iloc[i]

        # Demand zone: bullish candle + continuation
        if curr_close > curr_open and next_close > curr_close:
            zone_low = df["low"].iloc[i]
            zone_high = curr_open + atr_val * 0.2
            zone_high = max(zone_high, zone_low + body * 0.6)
            zones.append(Zone(
                zone_type="demand",
                low=zone_low,
                high=zone_high,
                strength=body_ratio,
                candle_index=i,
                total_candles=n,
            ))

        # Supply zone: bearish candle + continuation
        elif curr_close < curr_open and next_close < curr_close:
            zone_high = df["high"].iloc[i]
            zone_low = curr_open - atr_val * 0.2
            zone_low = min(zone_low, zone_high - body * 0.6)
            zones.append(Zone(
                zone_type="supply",
                low=zone_low,
                high=zone_high,
                strength=body_ratio,
                candle_index=i,
                total_candles=n,
            ))

    # Count tests and dwell for each zone
    for z in zones:
        _count_tests_and_dwell(z, df)

    return zones


def _count_tests_and_dwell(zone: Zone, df: pd.DataFrame):
    """Count how many times price re-entered the zone after leaving, and
    how many consecutive candles price dwelled inside."""
    inside = False
    tests = 0
    max_dwell = 0
    current_dwell = 0

    for i in range(zone.candle_index + 2, len(df)):
        price = df["close"].iloc[i]
        in_zone = zone.low <= price <= zone.high

        if in_zone:
            current_dwell += 1
            if not inside:
                tests += 1
                inside = True
        else:
            max_dwell = max(max_dwell, current_dwell)
            current_dwell = 0
            inside = False

    max_dwell = max(max_dwell, current_dwell)
    zone.tests = tests
    zone.dwell = max_dwell


def grade_zone(zone: Zone, current_price: float) -> str:
    age = zone.total_candles - 1 - zone.candle_index  # candles since formation

    # C-grade disqualifiers
    if zone.tests >= 2:
        zone.grade = "C"
        zone.score_contribution = 0.0
        return "C"
    if zone.strength < B_GRADE_BODY_ATR:
        zone.grade = "C"
        zone.score_contribution = 0.0
        return "C"
    if age > B_GRADE_MAX_AGE:
        zone.grade = "C"
        zone.score_contribution = 0.0
        return "C"
    if zone.dwell > DWELL_LIMIT:
        zone.grade = "C"
        zone.score_contribution = 0.0
        return "C"

    # A-grade check
    approach_distance = abs(current_price - (zone.low + zone.high) / 2) / PIP_SIZE
    if (zone.strength >= A_GRADE_BODY_ATR and
            zone.tests <= A_GRADE_MAX_TESTS and
            age <= A_GRADE_MAX_AGE and
            approach_distance >= APPROACH_PIPS):
        zone.grade = "A"
        zone.score_contribution = 1.5
        return "A"

    # B-grade
    if (zone.strength >= B_GRADE_BODY_ATR and
            zone.tests <= B_GRADE_MAX_TESTS and
            age <= B_GRADE_MAX_AGE):
        zone.grade = "B"
        zone.score_contribution = 1.0
        return "B"

    zone.grade = "C"
    zone.score_contribution = 0.0
    return "C"


def get_active_zones(df: pd.DataFrame, current_price: float) -> dict:
    raw_zones = detect_zones(df)

    # Invalidate zones price has closed through
    for z in raw_zones:
        if z.zone_type == "demand" and current_price < z.low - ZONE_INVALIDATION_PIPS * PIP_SIZE:
            z.invalidated = True
        elif z.zone_type == "supply" and current_price > z.high + ZONE_INVALIDATION_PIPS * PIP_SIZE:
            z.invalidated = True

    # Grade all non-invalidated zones
    active = []
    for z in raw_zones:
        if z.invalidated:
            continue
        grade_zone(z, current_price)
        if z.grade != "C":  # Skip C-grade entirely
            active.append(z)

    # Keep top N per direction, sorted by grade then strength
    grade_order = {"A": 0, "B": 1}
    demand = sorted(
        [z for z in active if z.zone_type == "demand"],
        key=lambda z: (grade_order.get(z.grade, 9), -z.strength, -z.candle_index),
    )[:MAX_ZONES_PER_DIRECTION]

    supply = sorted(
        [z for z in active if z.zone_type == "supply"],
        key=lambda z: (grade_order.get(z.grade, 9), -z.strength, -z.candle_index),
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


def format_zone_log(zone: Zone) -> str:
    age = zone.total_candles - 1 - zone.candle_index
    fresh = "fresh" if zone.tests == 0 else f"tested {zone.tests}x"
    return (f"Zone grade: {zone.grade} | {fresh} | "
            f"body={zone.strength:.1f}xATR | age={age} candles | "
            f"dwell={zone.dwell}")


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
    print(f"Demand zones (A/B only): {len(zones['demand'])}")
    print(f"Supply zones (A/B only): {len(zones['supply'])}")
    for z in zones["demand"][:3]:
        print(f"  {format_zone_log(z)}")
    for z in zones["supply"][:3]:
        print(f"  {format_zone_log(z)}")
