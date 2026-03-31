import logging
from typing import Optional

import pandas as pd

from strategy.base import Signal
from config.settings import SCALPER_SL_PIPS, SCALPER_TP_PIPS
from core.market import calc_atr, calc_ema
from confluence.zone_detector import (
    get_active_zones, price_in_zone, nearest_zone_above,
    nearest_zone_below, resample_to_h4, Zone,
)
from confluence.trend_filter import get_h1_trend, slope_matches_direction
from confluence.trigger import detect_trigger
from confluence.momentum import check_momentum
from core.news_filter import is_news_blocked

logger = logging.getLogger("astra.scorer")

PIP_SIZE = 0.1
MIN_SL_PIPS = 15
MAX_SL_PIPS = 40
MIN_SCORE = 3


class ConfluenceScorer:
    def __init__(self, symbol: str, mode: str = "live"):
        self.symbol = symbol
        self.mode = mode

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < 80:
            return None

        close = df["close"].iloc[-1]
        atr = calc_atr(df, 14)
        atr_val = atr.iloc[-1]
        if atr_val <= 0:
            return None

        # Resample to H4 for zone detection
        h4 = resample_to_h4(df)
        if len(h4) < 16:
            return None

        zones = get_active_zones(h4, close)

        # Detect M15 trigger pattern first — this determines direction
        trigger = detect_trigger(df)
        if not trigger["triggered"]:
            return None

        direction = trigger["direction"]
        score = 0
        layers = []

        # Layer 1: price inside matching H4 zone
        if direction == "BUY":
            zone_hit = price_in_zone(close, zones["demand"])
        else:
            zone_hit = price_in_zone(close, zones["supply"])

        if zone_hit:
            score += 1
            layers.append(f"L1:zone({zone_hit.zone_type} {zone_hit.low:.2f}-{zone_hit.high:.2f})")
        else:
            layers.append("L1:no_zone")

        # Layer 2: H1 EMA20 trend slope matches direction
        trend = get_h1_trend(df)
        if slope_matches_direction(trend, direction):
            score += 1
            layers.append(f"L2:trend({trend['direction']})")
        else:
            layers.append(f"L2:no_trend({trend['direction']})")

        # Layer 3: trigger pattern (already confirmed above)
        score += 1
        layers.append(f"L3:{trigger['pattern']}")

        # Layer 4: momentum (RSI + volume)
        mom = check_momentum(df)
        if mom["passed"]:
            score += 1
            layers.append(f"L4:momentum({mom['reason']})")
        else:
            layers.append(f"L4:no_mom({mom['reason']})")

        # Layer 5: no high-impact news within 30 min
        if self.mode == "backtest":
            news_clear = True
        else:
            news = is_news_blocked(self.symbol)
            news_clear = not news["blocked"]

        if news_clear:
            score += 1
            layers.append("L5:news_clear")
        else:
            layers.append("L5:news_blocked")

        layer_str = " | ".join(layers)
        logger.info(f"Confluence {self.symbol}: score={score}/5 {direction} [{layer_str}]")

        # Require minimum score AND both trigger + trend must be present
        has_trigger = trigger["triggered"]  # always true at this point
        has_trend = slope_matches_direction(trend, direction)
        if score < MIN_SCORE or not has_trend:
            return None

        # Build signal with zone-aware SL/TP
        sl_price, tp_price, sl_pips, tp_pips = self._calc_sl_tp(
            direction, close, atr_val, zone_hit, zones
        )

        confidence = score / 5.0

        signal = Signal(
            direction=direction,
            symbol=self.symbol,
            entry_price=close,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            reason=f"Confluence {score}/5: {layer_str}",
            confidence=confidence,
        )

        logger.info(f"Signal: {signal}")
        return signal

    def _calc_sl_tp(self, direction: str, entry: float, atr_val: float,
                    entry_zone: Optional[Zone], zones: dict) -> tuple:
        atr_buffer = atr_val * 0.3

        if direction == "BUY":
            if entry_zone:
                raw_sl = entry_zone.low - atr_buffer
                sl_pips = min(max((entry - raw_sl) / PIP_SIZE, MIN_SL_PIPS), MAX_SL_PIPS)
            else:
                sl_pips = SCALPER_SL_PIPS
            sl_price = entry - sl_pips * PIP_SIZE

            # TP: nearest supply zone above, or config fallback
            nearest_supply = nearest_zone_above(entry, zones["supply"])
            if nearest_supply:
                tp_dist = (nearest_supply.low - entry) / PIP_SIZE
                tp_pips = tp_dist if tp_dist >= sl_pips * 2 else SCALPER_TP_PIPS
            else:
                tp_pips = SCALPER_TP_PIPS
            tp_price = entry + tp_pips * PIP_SIZE

        else:
            if entry_zone:
                raw_sl = entry_zone.high + atr_buffer
                sl_pips = min(max((raw_sl - entry) / PIP_SIZE, MIN_SL_PIPS), MAX_SL_PIPS)
            else:
                sl_pips = SCALPER_SL_PIPS
            sl_price = entry + sl_pips * PIP_SIZE

            nearest_demand = nearest_zone_below(entry, zones["demand"])
            if nearest_demand:
                tp_dist = (entry - nearest_demand.high) / PIP_SIZE
                tp_pips = tp_dist if tp_dist >= sl_pips * 2 else SCALPER_TP_PIPS
            else:
                tp_pips = SCALPER_TP_PIPS
            tp_price = entry - tp_pips * PIP_SIZE

        return round(sl_price, 2), round(tp_price, 2), round(sl_pips, 1), round(tp_pips, 1)


if __name__ == "__main__":
    from backtest.data_loader import generate_synthetic_data

    df = generate_synthetic_data("XAUUSD", "2025-11-01", "2026-01-15", "M15", 2000)
    scorer = ConfluenceScorer("XAUUSD", mode="backtest")

    signals = 0
    for i in range(200, min(500, len(df))):
        window = df.iloc[:i + 1]
        sig = scorer.evaluate(window)
        if sig:
            signals += 1
    print(f"Signals in {min(500, len(df)) - 200} candles: {signals}")
