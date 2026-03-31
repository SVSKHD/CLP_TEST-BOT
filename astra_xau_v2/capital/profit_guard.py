import logging
from datetime import datetime, timedelta
from config.settings import (
    PER_SYMBOL_DAILY_TARGET, DAILY_CAP, DAILY_FLOOR, SYMBOLS,
    PROP_FIRM_MAX_DAILY_DD, PROP_FIRM_MAX_TOTAL_DD, ACCOUNT_EQUITY,
)

logger = logging.getLogger("astra.profit_guard")

CONSECUTIVE_LOSS_LIMIT = 3
COOLDOWN_HOURS = 4

# --- New guard thresholds ---
DAILY_DD_HALT_PCT = 0.03          # 3% equity drop from session open → halt new entries
PEAK_DD_EMERGENCY_PCT = 0.045     # 4.5% drop from account equity peak → emergency stop
DAILY_LOSS_CAP_USD = 1500         # hard USD daily loss cap
CONSECUTIVE_LOSS_HALT = 3         # pause trading for 2 hours after 3 consecutive losses
CONSECUTIVE_LOSS_PAUSE_MINUTES = 120


class ProfitGuard:
    def __init__(self, symbols: list = None, initial_equity: float = None):
        self.symbols = symbols or SYMBOLS
        self.initial_equity = initial_equity or ACCOUNT_EQUITY
        self.daily_start_equity = self.initial_equity
        self.current_equity = self.initial_equity
        self.realized_pnl = {s: 0.0 for s in self.symbols}
        self.floating_pnl = {s: 0.0 for s in self.symbols}
        self.daily_pips = {s: 0.0 for s in self.symbols}
        self.trade_count = {s: 0 for s in self.symbols}
        self.status = {s: "ACTIVE" for s in self.symbols}
        self.global_status = "ACTIVE"
        self.consecutive_losses = {s: 0 for s in self.symbols}
        self.cooldown_until = {s: None for s in self.symbols}
        # New guard state
        self.session_open_equity = self.initial_equity
        self.account_peak_equity = self.initial_equity
        self.daily_loss_usd = 0.0
        self.halt_new_entries = False
        self.emergency_stop = False
        self.consec_loss_pause_until = None
        self._freeze_callbacks = []
        self._cap_callbacks = []
        self._emergency_callbacks = []

    def on_freeze(self, callback):
        self._freeze_callbacks.append(callback)

    def on_global_cap(self, callback):
        self._cap_callbacks.append(callback)

    def on_emergency_stop(self, callback):
        self._emergency_callbacks.append(callback)

    def update_realized(self, symbol: str, pnl: float, pips: float,
                        trade_time: datetime = None):
        if symbol not in self.realized_pnl:
            return

        self.realized_pnl[symbol] += pnl
        self.daily_pips[symbol] += pips
        self.trade_count[symbol] += 1

        # Track consecutive losses for circuit breaker
        if pnl < 0:
            self.consecutive_losses[symbol] += 1
            if self.consecutive_losses[symbol] >= CONSECUTIVE_LOSS_LIMIT:
                cooldown_end = (trade_time or datetime.utcnow()) + timedelta(hours=COOLDOWN_HOURS)
                self.cooldown_until[symbol] = cooldown_end
                logger.warning(
                    f"PAUSED {symbol}: {CONSECUTIVE_LOSS_LIMIT} consecutive losses, "
                    f"cooling off {COOLDOWN_HOURS}h until {cooldown_end}"
                )
        else:
            self.consecutive_losses[symbol] = 0

        # Track daily loss for USD cap
        if pnl < 0:
            self.daily_loss_usd += abs(pnl)

        logger.info(
            f"PnL update: {symbol} +${pnl:.2f} ({pips:.1f} pips) | "
            f"Total: ${self.realized_pnl[symbol]:.2f} | Pips: {self.daily_pips[symbol]:.1f} | "
            f"Consec losses: {self.consecutive_losses[symbol]} | Day loss: ${self.daily_loss_usd:.2f}"
        )

        # Run guard checks after every trade close
        self.run_guard_checks(equity=self.current_equity, current_time=trade_time)

        if self.realized_pnl[symbol] >= PER_SYMBOL_DAILY_TARGET and self.status[symbol] == "ACTIVE":
            self.status[symbol] = "FROZEN"
            logger.info(f"FROZEN: {symbol} hit ${PER_SYMBOL_DAILY_TARGET} target "
                        f"(actual: ${self.realized_pnl[symbol]:.2f})")
            for cb in self._freeze_callbacks:
                try:
                    cb(symbol, self.realized_pnl[symbol])
                except Exception as e:
                    logger.error(f"Freeze callback error: {e}")

        total = self.total_realized()
        if total >= DAILY_CAP and self.global_status == "ACTIVE":
            self.global_status = "GLOBAL_CAP"
            for s in self.symbols:
                self.status[s] = "FROZEN"
            logger.info(f"GLOBAL CAP HIT: ${total:.2f} >= ${DAILY_CAP}")
            for cb in self._cap_callbacks:
                try:
                    cb(total)
                except Exception as e:
                    logger.error(f"Cap callback error: {e}")

    def update_equity(self, equity: float):
        self.current_equity = equity

    def update_floating(self, symbol: str, floating: float):
        self.floating_pnl[symbol] = floating

    def is_halted(self) -> bool:
        """Daily DD halt or loss cap reached — no new entries today."""
        return self.halt_new_entries

    def is_emergency_stopped(self) -> bool:
        """Peak DD emergency — manual reset required."""
        return self.emergency_stop

    def is_paused(self, current_time: datetime = None) -> bool:
        """Consecutive loss pause active."""
        if self.consec_loss_pause_until is None:
            return False
        now = current_time or datetime.utcnow()
        if now >= self.consec_loss_pause_until:
            self.consec_loss_pause_until = None
            return False
        return True

    def run_guard_checks(self, equity: float = None, current_time: datetime = None):
        """Run all new guard checks on every trade close or equity poll."""
        eq = equity if equity is not None else self.current_equity
        now = current_time or datetime.utcnow()

        # Update peak equity
        if eq > self.account_peak_equity:
            self.account_peak_equity = eq

        # 1) Daily DD halt (3% from session open)
        if self.session_open_equity > 0:
            daily_dd = (self.session_open_equity - eq) / self.session_open_equity
            if daily_dd >= DAILY_DD_HALT_PCT and not self.halt_new_entries:
                self.halt_new_entries = True
                logger.warning(
                    f"DAILY DD HALT: {daily_dd*100:.2f}% drop from session open "
                    f"${self.session_open_equity:,.2f} → ${eq:,.2f}"
                )

        # 2) Peak DD emergency (4.5% from peak)
        if self.account_peak_equity > 0:
            peak_dd = (self.account_peak_equity - eq) / self.account_peak_equity
            if peak_dd >= PEAK_DD_EMERGENCY_PCT and not self.emergency_stop:
                self.emergency_stop = True
                logger.critical(
                    f"PEAK DD EMERGENCY STOP: {peak_dd*100:.2f}% drop from peak "
                    f"${self.account_peak_equity:,.2f} → ${eq:,.2f} — manual reset required"
                )
                for cb in self._emergency_callbacks:
                    try:
                        cb("PEAK_DD_EMERGENCY", eq, peak_dd * 100)
                    except Exception as e:
                        logger.error(f"Emergency callback error: {e}")

        # 3) Daily loss cap (USD)
        if self.daily_loss_usd >= DAILY_LOSS_CAP_USD and not self.halt_new_entries:
            self.halt_new_entries = True
            logger.warning(f"DAILY LOSS CAP: ${self.daily_loss_usd:,.2f} >= ${DAILY_LOSS_CAP_USD}")

        # 4) Consecutive loss pause (2 hours)
        # (uses global consecutive loss counter across all symbols)
        total_consec = max(self.consecutive_losses.values()) if self.consecutive_losses else 0
        if total_consec >= CONSECUTIVE_LOSS_HALT and self.consec_loss_pause_until is None:
            self.consec_loss_pause_until = now + timedelta(minutes=CONSECUTIVE_LOSS_PAUSE_MINUTES)
            logger.warning(
                f"CONSECUTIVE LOSS PAUSE: {total_consec} losses — "
                f"pausing {CONSECUTIVE_LOSS_PAUSE_MINUTES}min until {self.consec_loss_pause_until}"
            )

    def check_drawdown(self, equity: float = None) -> dict:
        eq = equity if equity is not None else self.current_equity

        # 5% daily drawdown check
        daily_dd_limit = self.daily_start_equity * (1 - PROP_FIRM_MAX_DAILY_DD)
        if eq < daily_dd_limit:
            dd_pct = (self.daily_start_equity - eq) / self.daily_start_equity * 100
            if self.global_status not in ("EMERGENCY_STOP", "ACCOUNT_BREACH"):
                self.global_status = "EMERGENCY_STOP"
                for s in self.symbols:
                    self.status[s] = "FROZEN"
                logger.critical(
                    f"EMERGENCY STOP: daily DD {dd_pct:.2f}% — "
                    f"equity ${eq:,.2f} < limit ${daily_dd_limit:,.2f}"
                )
                for cb in self._emergency_callbacks:
                    try:
                        cb("EMERGENCY_STOP", eq, dd_pct)
                    except Exception as e:
                        logger.error(f"Emergency callback error: {e}")
            return {
                "breach": True,
                "type": "EMERGENCY_STOP",
                "equity": eq,
                "dd_pct": dd_pct,
            }

        # 10% total drawdown check
        total_dd_limit = self.initial_equity * (1 - PROP_FIRM_MAX_TOTAL_DD)
        if eq < total_dd_limit:
            dd_pct = (self.initial_equity - eq) / self.initial_equity * 100
            if self.global_status != "ACCOUNT_BREACH":
                self.global_status = "ACCOUNT_BREACH"
                for s in self.symbols:
                    self.status[s] = "FROZEN"
                logger.critical(
                    f"ACCOUNT BREACH: total DD {dd_pct:.2f}% — "
                    f"equity ${eq:,.2f} < limit ${total_dd_limit:,.2f}"
                )
                for cb in self._emergency_callbacks:
                    try:
                        cb("ACCOUNT_BREACH", eq, dd_pct)
                    except Exception as e:
                        logger.error(f"Emergency callback error: {e}")
            return {
                "breach": True,
                "type": "ACCOUNT_BREACH",
                "equity": eq,
                "dd_pct": dd_pct,
            }

        return {"breach": False}

    def start_new_day(self, equity: float = None):
        eq = equity if equity is not None else self.current_equity
        self.daily_start_equity = eq
        self.session_open_equity = eq
        self.halt_new_entries = False
        self.daily_loss_usd = 0.0

    def total_realized(self) -> float:
        return sum(self.realized_pnl.values())

    def total_floating(self) -> float:
        return sum(self.floating_pnl.values())

    def total_pnl(self) -> float:
        return self.total_realized() + self.total_floating()

    def is_symbol_active(self, symbol: str) -> bool:
        return self.status.get(symbol) == "ACTIVE"

    def is_global_active(self) -> bool:
        return self.global_status == "ACTIVE"

    def can_trade(self, symbol: str, current_time: datetime = None) -> dict:
        # New guard checks first
        if self.is_emergency_stopped():
            return {"allowed": False, "reason": "PEAK DD emergency stop — manual reset required"}
        if self.is_halted():
            return {"allowed": False, "reason": "Daily DD halt or loss cap — no new entries today"}
        if self.is_paused(current_time):
            return {"allowed": False, "reason": "Consecutive loss pause active"}
        # Existing checks
        if self.global_status in ("EMERGENCY_STOP", "ACCOUNT_BREACH"):
            return {"allowed": False, "reason": f"{self.global_status}: drawdown breach"}
        if self.global_status == "GLOBAL_CAP":
            return {"allowed": False, "reason": f"Global cap hit (${self.total_realized():.2f})"}
        if self.status.get(symbol) == "FROZEN":
            return {"allowed": False, "reason": f"{symbol} frozen (${self.realized_pnl[symbol]:.2f})"}
        from config.settings import DAILY_PIPS_COVERAGE
        if self.daily_pips.get(symbol, 0) >= DAILY_PIPS_COVERAGE:
            return {"allowed": False, "reason": f"{symbol} hit {DAILY_PIPS_COVERAGE} pip coverage"}
        # Consecutive loss cooldown check (per-symbol)
        cooldown = self.cooldown_until.get(symbol)
        if cooldown is not None:
            now = current_time or datetime.utcnow()
            if now < cooldown:
                return {"allowed": False,
                        "reason": f"{symbol} cooling off until {cooldown} ({self.consecutive_losses[symbol]} consec losses)"}
            else:
                self.cooldown_until[symbol] = None
                self.consecutive_losses[symbol] = 0
        return {"allowed": True, "reason": "OK"}

    def check_floor_alert(self) -> dict:
        total = self.total_realized()
        if total < DAILY_FLOOR:
            return {
                "alert": True,
                "message": f"Below daily floor: ${total:.2f} < ${DAILY_FLOOR}",
                "deficit": DAILY_FLOOR - total,
            }
        return {"alert": False}

    def get_summary(self) -> dict:
        return {
            "global_status": self.global_status,
            "total_realized": self.total_realized(),
            "total_floating": self.total_floating(),
            "cap_distance": DAILY_CAP - self.total_realized(),
            "floor_distance": DAILY_FLOOR - self.total_realized(),
            "symbols": {
                s: {
                    "status": self.status[s],
                    "realized": self.realized_pnl[s],
                    "floating": self.floating_pnl[s],
                    "pips": self.daily_pips[s],
                    "trades": self.trade_count[s],
                }
                for s in self.symbols
            },
        }

    def reset(self):
        for s in self.symbols:
            self.realized_pnl[s] = 0.0
            self.floating_pnl[s] = 0.0
            self.daily_pips[s] = 0.0
            self.trade_count[s] = 0
            self.status[s] = "ACTIVE"
            # Don't reset consecutive_losses or cooldown — they persist across days
        self.global_status = "ACTIVE"
        self.daily_start_equity = self.current_equity
        # Reset daily guards (but NOT emergency_stop — that requires manual reset)
        self.halt_new_entries = False
        self.daily_loss_usd = 0.0
        self.session_open_equity = self.current_equity
        logger.info("ProfitGuard reset for new day")

    def to_dict(self) -> dict:
        return {
            "global_status": self.global_status,
            "realized_pnl": dict(self.realized_pnl),
            "floating_pnl": dict(self.floating_pnl),
            "daily_pips": dict(self.daily_pips),
            "trade_count": dict(self.trade_count),
            "status": dict(self.status),
        }

    def from_dict(self, data: dict):
        self.global_status = data.get("global_status", "ACTIVE")
        self.realized_pnl.update(data.get("realized_pnl", {}))
        self.floating_pnl.update(data.get("floating_pnl", {}))
        self.daily_pips.update(data.get("daily_pips", {}))
        self.trade_count.update(data.get("trade_count", {}))
        self.status.update(data.get("status", {}))


if __name__ == "__main__":
    guard = ProfitGuard()

    guard.on_freeze(lambda sym, pnl: print(f"  >>> FREEZE CALLBACK: {sym} at ${pnl:.2f}"))
    guard.on_global_cap(lambda total: print(f"  >>> GLOBAL CAP CALLBACK: ${total:.2f}"))

    guard.update_realized("XAUUSD", 150, 30)
    guard.update_realized("XAUUSD", 160, 32)
    print(f"XAUUSD status: {guard.status['XAUUSD']}")
    print(f"Can trade XAUUSD: {guard.can_trade('XAUUSD')}")

    guard.update_realized("XAUEUR", 310, 55)
    print(f"XAUEUR status: {guard.status['XAUEUR']}")

    print(f"\nSummary: {guard.get_summary()}")
