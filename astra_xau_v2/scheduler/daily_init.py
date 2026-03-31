import logging
import os
import sys
import threading
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.settings import (
    SYMBOLS, MT5_SERVER, MT5_LOGIN, MT5_PASSWORD, MONGO_URI,
    ACCOUNT_EQUITY, DAILY_FLOOR,
)
from core.mt5_client import initialize, shutdown, get_account_info
from capital.profit_guard import ProfitGuard
from executor.runner import Runner
from state.manager import reset_all
from logger.mongo_logger import init_mongo
from logger.notifier import Notifier
from monitor.dashboard import Dashboard
from monitor.live_chart import LiveChart
from monitor import mt5_chart_bridge

import logging.handlers

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.TimedRotatingFileHandler(
            os.path.join(LOG_DIR, "astra.log"),
            when="midnight",
            backupCount=30,
        ),
    ],
)

logger = logging.getLogger("astra.scheduler")

_runner = None
_dashboard = None
_live_chart = None

# --- High-impact news day skip list ---
HIGH_IMPACT_NEWS_DAYS = []


def load_news_skip_days(news_csv_path: str) -> list:
    """
    Expects a CSV with column 'date' (YYYY-MM-DD) and 'event' (string).
    Filters for: CPI, NFP, FOMC, Fed, Nonfarm, Interest Rate.
    Returns list of date strings to skip entirely.
    """
    try:
        import pandas as pd
        df = pd.read_csv(news_csv_path)
        keywords = ['CPI', 'NFP', 'FOMC', 'Fed', 'Nonfarm', 'Interest Rate']
        mask = df['event'].str.contains('|'.join(keywords), case=False, na=False)
        return df[mask]['date'].tolist()
    except Exception as e:
        logger.warning(f"NEWS_CALENDAR not configured — skipping news filter: {e}")
        return []


def is_news_skip_day(date_str: str) -> bool:
    return date_str in HIGH_IMPACT_NEWS_DAYS


def daily_reset():
    global _runner, _dashboard, _live_chart
    logger.info("=" * 60)
    logger.info("DAILY RESET STARTING")
    logger.info("=" * 60)

    if _runner and _runner.is_running():
        logger.info("Stopping previous runner...")
        _runner.stop()

    for sym in SYMBOLS:
        try:
            mt5_chart_bridge.clear_symbol_objects(sym)
        except Exception as e:
            logger.debug(f"Chart clear error {sym}: {e}")

    reset_all(SYMBOLS)
    logger.info("State files reset")

    equity = ACCOUNT_EQUITY
    try:
        account = get_account_info()
        equity = account["equity"]
        logger.info(f"Live equity: ${equity:,.2f}")
    except Exception as e:
        logger.warning(f"Could not fetch live equity: {e}")

    # Load news calendar if available
    global HIGH_IMPACT_NEWS_DAYS
    news_csv = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "news_calendar.csv")
    if os.path.exists(news_csv):
        HIGH_IMPACT_NEWS_DAYS = load_news_skip_days(news_csv)
        logger.info(f"Loaded {len(HIGH_IMPACT_NEWS_DAYS)} high-impact news days")
    else:
        logger.warning("NEWS_CALENDAR not configured — skipping news filter")

    notifier = Notifier()
    notifier.send_day_start(equity, 0, SYMBOLS)

    # Check if today is a high-impact news day
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    if is_news_skip_day(today_str):
        logger.warning(f"HIGH_IMPACT_NEWS_DAY: {today_str} — halting new entries for the day")
        notifier.send(f"HIGH_IMPACT_NEWS_DAY: {today_str} — no trading today", level="warn")

    _runner = Runner(SYMBOLS, mode="live")
    _runner.set_notifier(notifier)

    profit_guard = _runner.profit_guard
    profit_guard.on_freeze(lambda sym, pnl: notifier.send_freeze(sym, pnl))
    profit_guard.on_global_cap(lambda total: notifier.send_global_cap(total))

    # If news skip day, halt the profit guard before any trading starts
    if is_news_skip_day(today_str):
        profit_guard.halt_new_entries = True

    _runner.start(interval=5.0)
    logger.info("Runner started for all symbols")

    _dashboard = Dashboard(profit_guard=profit_guard)
    dash_thread = threading.Thread(target=_dashboard.run, args=(5.0,),
                                   daemon=True, name="dashboard")
    dash_thread.start()
    logger.info("Terminal dashboard started")

    _live_chart = LiveChart(profit_guard=profit_guard)
    _live_chart.start()
    logger.info("Live chart server started at http://localhost:8050")


def floor_check():
    if _runner:
        alert = _runner.profit_guard.check_floor_alert()
        if alert["alert"]:
            logger.warning(alert["message"])
            notifier = Notifier()
            notifier.send_floor_alert(
                _runner.profit_guard.total_realized(),
                alert["deficit"]
            )


def main():
    logger.info("Astra XAU v2 Scheduler starting")

    if MT5_SERVER and MT5_LOGIN:
        try:
            initialize(MT5_SERVER, MT5_LOGIN, MT5_PASSWORD)
            logger.info("MT5 connected")
        except Exception as e:
            logger.error(f"MT5 connection failed: {e}")
            logger.error("Cannot start without MT5. Check credentials in .env")
            return
    else:
        logger.warning("MT5 credentials not set. Configure MT5_SERVER, MT5_LOGIN, MT5_PASSWORD in .env")
        return

    if MONGO_URI:
        init_mongo(MONGO_URI)

    daily_reset()

    scheduler = BlockingScheduler()
    scheduler.add_job(daily_reset, "cron", hour=0, minute=1, id="daily_reset")
    scheduler.add_job(floor_check, "cron", hour=18, minute=0, id="floor_check")

    logger.info("Scheduler active. Daily reset at 00:01, floor check at 18:00")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down")
        if _runner:
            _runner.stop()
        if _dashboard:
            _dashboard.stop()
        shutdown()


if __name__ == "__main__":
    main()
