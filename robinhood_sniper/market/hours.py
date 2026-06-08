from __future__ import annotations

import datetime
from enum import Enum, auto

import pytz

from robinhood_sniper import config


class Session(Enum):
    PREMARKET   = auto()   # 04:00 – 09:30 ET
    REGULAR     = auto()   # 09:30 – 16:00 ET
    AFTERMARKET = auto()   # 16:00 – 20:00 ET
    CLOSED      = auto()   # 20:00 – 04:00 ET  (stocks unavailable)
    CRYPTO_ONLY = auto()   # alias used when stocks are closed


def now_et() -> datetime.datetime:
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.datetime.now(tz)


def _hhmm(dt: datetime.datetime, time_str: str) -> datetime.datetime:
    """Return a timezone-aware datetime for today at HH:MM ET."""
    tz = pytz.timezone(config.TIMEZONE)
    h, m = map(int, time_str.split(":"))
    naive = dt.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=None)
    return tz.localize(naive)


def current_session() -> Session:
    now = now_et()
    premarket_start  = _hhmm(now, config.PREMARKET_START)
    market_open      = _hhmm(now, config.MARKET_OPEN)
    market_close     = _hhmm(now, config.MARKET_CLOSE)
    aftermarket_end  = _hhmm(now, config.AFTERMARKET_END)

    if premarket_start <= now < market_open:
        return Session.PREMARKET
    if market_open <= now < market_close:
        return Session.REGULAR
    if market_close <= now < aftermarket_end:
        return Session.AFTERMARKET
    return Session.CLOSED


def stocks_tradeable() -> bool:
    s = current_session()
    if s == Session.REGULAR:
        return True
    if config.TRADE_EXTENDED_HOURS and s in (Session.PREMARKET, Session.AFTERMARKET):
        return True
    return False


def extended_hours_active() -> bool:
    return current_session() in (Session.PREMARKET, Session.AFTERMARKET)


def is_weekend() -> bool:
    return now_et().weekday() >= 5  # Saturday = 5, Sunday = 6


def stocks_available() -> bool:
    """Stocks can only trade Mon–Fri during eligible sessions."""
    if is_weekend():
        return False
    return stocks_tradeable()


def crypto_available() -> bool:
    """Crypto trades 24/7."""
    return True
