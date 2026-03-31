import logging
import time
from datetime import datetime, timedelta

import requests

from config.settings import (
    NEWS_BLOCK_MINUTES_BEFORE,
    NEWS_BLOCK_MINUTES_AFTER,
    HIGH_IMPACT_ONLY,
)

logger = logging.getLogger("astra.news_filter")

_cached_events = []
_cache_time = None
_CACHE_TTL = 3600


def fetch_news_events(force_refresh: bool = False) -> list:
    global _cached_events, _cache_time

    if not force_refresh and _cache_time and (time.time() - _cache_time) < _CACHE_TTL:
        return _cached_events

    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        events = resp.json()

        parsed = []
        for ev in events:
            impact = ev.get("impact", "").lower()
            if HIGH_IMPACT_ONLY and impact not in ("high", "holiday"):
                continue

            dt_str = ev.get("date", "")
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            parsed.append({
                "title": ev.get("title", ""),
                "country": ev.get("country", ""),
                "impact": impact,
                "datetime": dt,
                "currency": ev.get("country", "").upper(),
            })

        _cached_events = parsed
        _cache_time = time.time()
        logger.info(f"Fetched {len(parsed)} high-impact news events")
        return parsed

    except Exception as e:
        logger.warning(f"News fetch failed (trading continues): {e}")
        return _cached_events


def is_news_blocked(symbol: str, now: datetime = None) -> dict:
    if now is None:
        now = datetime.utcnow()

    events = fetch_news_events()
    currencies = _symbol_currencies(symbol)

    before = timedelta(minutes=NEWS_BLOCK_MINUTES_BEFORE)
    after = timedelta(minutes=NEWS_BLOCK_MINUTES_AFTER)

    for ev in events:
        ev_time = ev["datetime"].replace(tzinfo=None) if ev["datetime"].tzinfo else ev["datetime"]
        if ev.get("currency", "") in currencies or ev.get("country", "").upper() in currencies:
            if (ev_time - before) <= now <= (ev_time + after):
                return {
                    "blocked": True,
                    "event": ev["title"],
                    "event_time": ev_time,
                    "unblock_time": ev_time + after,
                    "currency": ev.get("currency", ""),
                }

    return {"blocked": False}


def _symbol_currencies(symbol: str) -> set:
    currencies = {"USD"}
    if "EUR" in symbol:
        currencies.add("EUR")
    if "GBP" in symbol:
        currencies.add("GBP")
    if "XAU" in symbol:
        currencies.add("USD")
    return currencies


def get_next_news_event(symbol: str, now: datetime = None) -> dict:
    if now is None:
        now = datetime.utcnow()

    events = fetch_news_events()
    currencies = _symbol_currencies(symbol)

    future = []
    for ev in events:
        ev_time = ev["datetime"].replace(tzinfo=None) if ev["datetime"].tzinfo else ev["datetime"]
        if ev_time > now:
            if ev.get("currency", "") in currencies or ev.get("country", "").upper() in currencies:
                future.append(ev)

    if future:
        future.sort(key=lambda x: x["datetime"])
        return future[0]
    return None


if __name__ == "__main__":
    events = fetch_news_events(force_refresh=True)
    print(f"Loaded {len(events)} events")
    for ev in events[:5]:
        print(f"  {ev['datetime']} | {ev['impact']} | {ev['title']} ({ev.get('currency', '')})")

    for sym in ["XAUUSD", "XAUEUR", "XAUGBP"]:
        status = is_news_blocked(sym)
        print(f"{sym} news blocked: {status['blocked']}")
