import logging
from datetime import datetime, timedelta
from typing import Optional, List

import pandas as pd

from strategy.base import Signal
from config.settings import SCALPER_SL_PIPS, SCALPER_TP_PIPS
from core.market import calc_atr, calc_ema
from confluence.zone_detector import (
    get_active_zones, price_in_zone, nearest_zone_above,
    nearest_zone_below, resample_to_h4, Zone, format_zone_log,
)
from confluence.trend_filter import get_h1_trend, slope_matches_direction
from confluence.trigger import detect_trigger
from confluence.momentum import check_momentum
from core.news_filter import is_news_blocked

logger = logging.getLogger("astra.scorer")

PIP_SIZE = 0.1
MIN_SL_PIPS = 15
MAX_SL_PIPS = 40
SCORE_THRESHOLD = 3.0   # out of max 5.5 (with A-grade zone)
SL_STREAK_LIMIT = 3     # 3 consecutive SL hits → pause
SL_STREAK_PAUSE_HOURS = 6


class ConfluenceScorer:
    def __init__(self, symbol: str, mode: str = "live"):
        self.symbol = symbol
        self.mode = mode
        self.recent_results: List[str] = []  # track last N trade results
        self.sl_pause_until: Optional[datetime] = None

    def record_trade_result(self, result: str, trade_time: datetime = None):
        self.recent_results.append(result)
        if len(self.recent_results) > 10:
            self.recent_results = self.recent_results[-10:]

        # Check last 3 results for SL streak
        last_3 = self.recent_results[-SL_STREAK_LIMIT:]
        if len(last_3) >= SL_STREAK_LIMIT and all(r == "LOSS" for r in last_3):
            self.sl_pause_until = (trade_time or datetime.utcnow()) + timedelta(hours=SL_STREAK_PAUSE_HOURS)
            logger.warning(
                f"SL STREAK PAUSE {self.symbol}: {SL_STREAK_LIMIT} consecutive SL hits, "
                f"pausing {SL_STREAK_PAUSE_HOURS}h until {self.sl_pause_until}"
            )

    def is_paused(self, current_time: datetime = None) -> bool:
        if self.sl_pause_until is None:
            return False
        now = current_time or datetime.utcnow()
        if now >= self.sl_pause_until:
            self.sl_pause_until = None
            self.recent_results.clear()
            return False
        return True

    def evaluate(self, df: pd.DataFrame, current_time: datetime = None) -> Optional[Signal]:
        if len(df) < 80:
            return None

        if self.is_paused(current_time):
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
        score = 0.0
        layers = []

        # Layer 1: price inside matching H4 zone (A=1.5, B=1.0, C/none=0)
        if direction == "BUY":
            zone_hit = price_in_zone(close, zones["demand"])
        else:
            zone_hit = price_in_zone(close, zones["supply"])

        if zone_hit:
            score += zone_hit.score_contribution
            zone_log = format_zone_log(zone_hit)
            layers.append(f"L1:{zone_hit.grade}-zone(+{zone_hit.score_contribution}) {zone_log}")
        else:
            layers.append("L1:no_zone(+0)")

        # Layer 2: H1 EMA20 trend slope matches direction → +1
        trend = get_h1_trend(df)
        if slope_matches_direction(trend, direction):
            score += 1.0
            layers.append(f"L2:trend({trend['direction']})")
        else:
            layers.append(f"L2:no_trend({trend['direction']})")

        # Layer 3: trigger pattern (already confirmed) → +1
        score += 1.0
        layers.append(f"L3:{trigger['pattern']}")

        # Layer 4: momentum (RSI + volume) → +1
        mom = check_momentum(df)
        if mom["passed"]:
            score += 1.0
            layers.append(f"L4:momentum({mom['reason']})")
        else:
            layers.append(f"L4:no_mom({mom['reason']})")

        # Layer 5: no high-impact news → +1
        if self.mode == "backtest":
            news_clear = True
        else:
            news = is_news_blocked(self.symbol)
            news_clear = not news["blocked"]

        if news_clear:
            score += 1.0
            layers.append("L5:news_clear")
        else:
            layers.append("L5:news_blocked")

        layer_str = " | ".join(layers)
        max_score = 5.5 if zone_hit and zone_hit.grade == "A" else 5.0
        logger.info(f"Confluence {self.symbol}: score={score:.1f}/{max_score:.1f} {direction} [{layer_str}]")

        # Gate: require trend (L2) + minimum score
        has_trend = slope_matches_direction(trend, direction)
        if not has_trend or score < SCORE_THRESHOLD:
            return None

        # Build signal with zone-aware SL/TP
        sl_price, tp_price, sl_pips, tp_pips = self._calc_sl_tp(
            direction, close, atr_val, zone_hit, zones
        )

        confidence = min(score / 5.5, 1.0)

        signal = Signal(
            direction=direction,
            symbol=self.symbol,
            entry_price=close,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            reason=f"Confluence {score:.1f}/{max_score:.1f}: {layer_str}",
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
