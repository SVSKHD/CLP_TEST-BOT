import os
from dotenv import load_dotenv

load_dotenv()

SYMBOLS = ["XAUUSD", "XAUEUR"]  # XAUGBP suspended pending strategy calibration

ACCOUNT_EQUITY = float(os.getenv("ACCOUNT_EQUITY", 50000))
PROP_FIRM_MAX_DAILY_DD = 0.05
PROP_FIRM_MAX_TOTAL_DD = 0.10

PER_SYMBOL_DAILY_TARGET = 300
DAILY_FLOOR = 500
DAILY_CAP = 3000
DAILY_PIPS_COVERAGE = 200

SCALPER_SL_PIPS = 20
SCALPER_TP_PIPS = 75

RISK_PER_TRADE_PCT = 0.01
MAX_CONCURRENT_TRADES = 1
MAGIC_NUMBER = 20260101

NEWS_BLOCK_MINUTES_BEFORE = 30
NEWS_BLOCK_MINUTES_AFTER = 30
HIGH_IMPACT_ONLY = True

MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")

MONGO_URI = os.getenv("MONGO_URI", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BACKTEST_START = os.getenv("BACKTEST_START", "2025-01-01")
BACKTEST_END = os.getenv("BACKTEST_END", "2025-12-31")
BACKTEST_TIMEFRAME = os.getenv("BACKTEST_TIMEFRAME", "M15")
BACKTEST_SPREAD_PIPS = float(os.getenv("BACKTEST_SPREAD_PIPS", 1.5))
BACKTEST_SLIPPAGE_PIPS = float(os.getenv("BACKTEST_SLIPPAGE_PIPS", 0.2))
BACKTEST_COMMISSION_USD = float(os.getenv("BACKTEST_COMMISSION_USD", 3.5))


if __name__ == "__main__":
    print("=== Astra XAU v2 Settings ===")
    print(f"Symbols: {SYMBOLS}")
    print(f"Account Equity: ${ACCOUNT_EQUITY:,.2f}")
    print(f"Daily Target Floor: ${DAILY_FLOOR}")
    print(f"Daily Cap: ${DAILY_CAP}")
    print(f"Risk per trade: {RISK_PER_TRADE_PCT*100}%")
    print(f"MT5 Server: {MT5_SERVER or '(not set)'}")
    print(f"Mongo URI: {'set' if MONGO_URI else '(not set)'}")
