"""
momentum_price.py
-----------------
Pure price movement strategy. Zero indicators.
Entry: $2.00 move in last 4 M5 candles with strong bodies
       breaking recent structure, not exhausted
SL:    $1.00 from entry
TP:    $2.00 from entry
RR:    2:1
"""

import pandas as pd
import logging
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone


@dataclass
class MomentumSignal:
    direction: str          # "BUY" or "SELL"
    entry: float            # entry price
    sl: float               # stop loss price
    tp: float               # take profit price
    lots: float             # lot size
    trigger_move: float     # how many dollars moved to trigger
    session_move: float     # total move since session open
    strong_candles: int     # how many strong body candles in trigger
    signal_time: datetime   # when signal generated


class MomentumPriceStrategy:
    """
    No indicators. Pure price movement.

    Entry logic:
    1. Session gate: London 07:00-10:00 GMT only
    2. Trigger: price moves $2.00+ in last 4 M5 candles
    3. Body strength: at least 2 of 4 candles have body >= 60% of range
    4. Not exhausted: total session move < $6.00
    5. Structure break: price must close beyond recent 10-bar swing high/low
    """

    # ── parameters ─────────────────────────────────────────────────────
    TRIGGER_MOVE_USD = 2.00       # minimum $ move to qualify
    TP_USD = 2.00                 # take profit distance in $
    SL_USD = 1.00                 # stop loss distance in $
    LOT_SIZE = 2.0                # standard lot size
    MIN_BODY_RATIO = 0.60         # candle body / total range minimum
    MIN_STRONG_CANDLES = 2        # of 4 trigger candles must be strong
    MAX_SESSION_MOVE_USD = 6.00   # if leg already moved this much → skip
    STRUCTURE_LOOKBACK = 10       # bars to look back for swing high/low
    CANDLE_LOOKBACK = 4           # bars to measure trigger move

    # Session: London only
    SESSION_START_HOUR = 7        # GMT
    SESSION_END_HOUR = 10         # GMT

    def __init__(self, lot_size: float = 2.0, logger_inst=None):
        self.lot_size = lot_size
        self.logger = logger_inst or logging.getLogger(__name__)
        self.last_signal_bar = None

    # ── public ─────────────────────────────────────────────────────────

    def get_signal(
        self,
        df_m5: pd.DataFrame,
        current_time_utc: Optional[datetime] = None,
        mode: str = "live",
    ) -> Optional[MomentumSignal]:
        """
        Main entry point. Returns MomentumSignal or None.
        df_m5 must have columns: open, high, low, close, time
        Minimum 20 rows required.
        """
        if len(df_m5) < 20:
            return None

        # Gate 1: session
        if not self._in_session(df_m5, current_time_utc):
            return None

        # Gate 2: trigger move
        trigger_move = self._get_trigger_move(df_m5)
        if abs(trigger_move) < self.TRIGGER_MOVE_USD:
            return None

        # Gate 3: candle body strength
        strong_count = self._count_strong_candles(df_m5)
        if strong_count < self.MIN_STRONG_CANDLES:
            self.logger.debug(
                f"Weak candles: only {strong_count} strong bodies — skip"
            )
            return None

        # Gate 4: not exhausted
        session_move = self._get_session_move(df_m5)
        if abs(session_move) >= self.MAX_SESSION_MOVE_USD:
            self.logger.debug(
                f"Leg exhausted: session moved ${abs(session_move):.2f} — skip"
            )
            return None

        # Gate 5: structure break
        current_price = df_m5["close"].iloc[-1]
        if not self._breaks_structure(df_m5, trigger_move):
            self.logger.debug(
                f"No structure break at {current_price:.2f} — skip"
            )
            return None

        # All gates passed — build signal
        direction = "BUY" if trigger_move > 0 else "SELL"

        if direction == "BUY":
            sl = current_price - self.SL_USD
            tp = current_price + self.TP_USD
        else:
            sl = current_price + self.SL_USD
            tp = current_price - self.TP_USD

        signal = MomentumSignal(
            direction=direction,
            entry=current_price,
            sl=sl,
            tp=tp,
            lots=self.lot_size,
            trigger_move=round(trigger_move, 2),
            session_move=round(session_move, 2),
            strong_candles=strong_count,
            signal_time=current_time_utc or datetime.now(timezone.utc),
        )

        self.logger.info(
            f"MOMENTUM SIGNAL: {direction} @ {current_price:.2f} | "
            f"trigger=${abs(trigger_move):.2f} | "
            f"session=${abs(session_move):.2f} | "
            f"strong_candles={strong_count} | "
            f"SL={sl:.2f} TP={tp:.2f}"
        )

        return signal

    # ── private ────────────────────────────────────────────────────────

    def _in_session(
        self,
        df_m5: pd.DataFrame,
        current_time_utc: Optional[datetime],
    ) -> bool:
        """London session 07:00-10:00 GMT only."""
        if current_time_utc is not None:
            hour = current_time_utc.hour
        else:
            last_time = pd.to_datetime(df_m5["time"].iloc[-1])
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            hour = last_time.hour

        in_session = self.SESSION_START_HOUR <= hour < self.SESSION_END_HOUR
        if not in_session:
            self.logger.debug(f"Outside session (hour={hour} UTC) — skip")
        return in_session

    def _get_trigger_move(self, df_m5: pd.DataFrame) -> float:
        """
        $ move over last CANDLE_LOOKBACK candles.
        Positive = bullish momentum, Negative = bearish.
        """
        last = df_m5.tail(self.CANDLE_LOOKBACK)
        return last.iloc[-1]["close"] - last.iloc[0]["open"]

    def _is_strong_candle(self, candle: pd.Series) -> bool:
        """
        Body must be >= 60% of total candle range.
        Eliminates wick-driven noise candles.
        """
        body = abs(candle["close"] - candle["open"])
        range_ = candle["high"] - candle["low"]
        if range_ == 0:
            return False
        return (body / range_) >= self.MIN_BODY_RATIO

    def _count_strong_candles(self, df_m5: pd.DataFrame) -> int:
        """Count strong body candles in last CANDLE_LOOKBACK bars."""
        last = df_m5.tail(self.CANDLE_LOOKBACK)
        return sum(self._is_strong_candle(last.iloc[i]) for i in range(len(last)))

    def _get_session_move(self, df_m5: pd.DataFrame) -> float:
        """
        Total $ move since London session opened (07:00 GMT).
        Used to detect exhausted legs.
        """
        df_copy = df_m5.copy()
        df_copy["_time"] = pd.to_datetime(df_copy["time"])

        session_bars = df_copy[df_copy["_time"].dt.hour >= self.SESSION_START_HOUR]

        if len(session_bars) == 0:
            return 0.0

        session_open = session_bars.iloc[0]["open"]
        current_close = df_m5.iloc[-1]["close"]
        return current_close - session_open

    def _breaks_structure(
        self,
        df_m5: pd.DataFrame,
        trigger_move: float,
    ) -> bool:
        """
        Price must close beyond recent swing high (BUY) or
        swing low (SELL). Prevents entries in middle of range.
        """
        structure_bars = df_m5.iloc[
            -(self.STRUCTURE_LOOKBACK + self.CANDLE_LOOKBACK) : -self.CANDLE_LOOKBACK
        ]

        if len(structure_bars) == 0:
            return True  # fail open

        current_close = df_m5["close"].iloc[-1]

        if trigger_move > 0:  # BUY — must break swing high
            swing_high = structure_bars["high"].max()
            result = current_close > swing_high
            self.logger.debug(
                f"Structure BUY: close={current_close:.2f} "
                f"swing_high={swing_high:.2f} breaks={result}"
            )
            return result
        else:  # SELL — must break swing low
            swing_low = structure_bars["low"].min()
            result = current_close < swing_low
            self.logger.debug(
                f"Structure SELL: close={current_close:.2f} "
                f"swing_low={swing_low:.2f} breaks={result}"
            )
            return result
