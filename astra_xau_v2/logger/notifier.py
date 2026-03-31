import logging
import requests

from config.settings import DISCORD_WEBHOOK, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("astra.notifier")


class Notifier:
    def __init__(self, discord_url: str = None, tg_token: str = None, tg_chat_id: str = None):
        self.discord_url = discord_url or DISCORD_WEBHOOK
        self.tg_token = tg_token or TELEGRAM_TOKEN
        self.tg_chat_id = tg_chat_id or TELEGRAM_CHAT_ID

    def send(self, message: str, level: str = "info"):
        prefix = self._level_prefix(level)
        full_msg = f"{prefix} {message}"

        if self.discord_url:
            self._send_discord(full_msg)
        if self.tg_token and self.tg_chat_id:
            self._send_telegram(full_msg)

    def _level_prefix(self, level: str) -> str:
        prefixes = {
            "info": "ℹ️",
            "warn": "⚠️",
            "error": "🔴",
            "success": "✅",
            "cap": "🏆",
            "freeze": "🧊",
        }
        return prefixes.get(level, "📊")

    def _send_discord(self, message: str):
        try:
            payload = {"content": message}
            resp = requests.post(self.discord_url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logger.debug(f"Discord send failed: {e}")

    def _send_telegram(self, message: str):
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": message, "parse_mode": "HTML"}
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logger.debug(f"Telegram send failed: {e}")

    def send_trade_open(self, symbol: str, direction: str, lot: float, price: float):
        self.send(
            f"<b>OPEN</b> {direction} {lot} {symbol} @ {price:.2f}",
            level="info",
        )

    def send_trade_close(self, symbol: str, pnl: float, pips: float, result: str):
        level = "success" if pnl > 0 else "error"
        self.send(
            f"<b>CLOSE</b> {symbol} {result} | PnL: ${pnl:.2f} | Pips: {pips:.1f}",
            level=level,
        )

    def send_freeze(self, symbol: str, pnl: float):
        self.send(
            f"<b>FROZEN</b> {symbol} target hit: ${pnl:.2f}",
            level="freeze",
        )

    def send_global_cap(self, total: float):
        self.send(
            f"<b>GLOBAL CAP HIT</b> ${total:.2f} — ALL TRADING STOPPED",
            level="cap",
        )

    def send_floor_alert(self, total: float, deficit: float):
        self.send(
            f"<b>FLOOR ALERT</b> Total: ${total:.2f} | Need: ${deficit:.2f} more to hit $500 floor",
            level="warn",
        )

    def send_day_start(self, equity: float, yesterday_pnl: float, symbols: list):
        self.send(
            f"<b>DAY START</b> Equity: ${equity:,.2f} | Yesterday: ${yesterday_pnl:.2f} | "
            f"Symbols: {', '.join(symbols)}",
            level="info",
        )


if __name__ == "__main__":
    notifier = Notifier()
    if notifier.discord_url or notifier.tg_token:
        notifier.send("Test notification from Astra XAU v2", level="info")
        print("Notification sent")
    else:
        print("No notification channels configured. Set DISCORD_WEBHOOK or TELEGRAM_TOKEN in .env")
