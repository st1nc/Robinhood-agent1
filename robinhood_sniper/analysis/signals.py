"""Technical-analysis signal engine using finta.

Consumes an OHLCV DataFrame and returns a scored Opportunity.
All trades are LONG-only (Robinhood does not support shorting stocks/crypto).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from finta import TA

from robinhood_sniper import config

log = logging.getLogger(__name__)


@dataclass
class Opportunity:
    symbol: str
    asset_type: str          # "stock" | "crypto"
    score: int               # 0–6
    price: float
    reasons: list[str] = field(default_factory=list)

    def __lt__(self, other: "Opportunity") -> bool:
        return self.score < other.score


def _build_df(raw: list[dict]) -> Optional[pd.DataFrame]:
    """Convert robin_stocks historicals list → clean OHLCV DataFrame."""
    if not raw or len(raw) < config.BB_PERIOD + 5:
        return None

    rows = []
    for bar in raw:
        try:
            rows.append({
                "open":   float(bar.get("open_price",  bar.get("open",  0))),
                "high":   float(bar.get("high_price",  bar.get("high",  0))),
                "low":    float(bar.get("low_price",   bar.get("low",   0))),
                "close":  float(bar.get("close_price", bar.get("close", 0))),
                "volume": float(bar.get("volume", 1)),
            })
        except (TypeError, ValueError):
            continue

    if len(rows) < config.BB_PERIOD + 5:
        return None

    df = pd.DataFrame(rows)
    df.replace(0, np.nan, inplace=True)
    df.dropna(inplace=True)
    if len(df) < config.BB_PERIOD + 5:
        return None
    return df


def _rsi_score(df: pd.DataFrame, reasons: list[str]) -> int:
    try:
        rsi = TA.RSI(df, period=config.RSI_PERIOD)
        if rsi is None or rsi.dropna().empty:
            return 0
        latest = float(rsi.iloc[-1])
        prev   = float(rsi.iloc[-2]) if len(rsi) > 1 else latest
        if latest < config.RSI_OVERSOLD:
            reasons.append(f"RSI={latest:.1f} deeply oversold")
            return 2
        if prev < config.RSI_OVERSOLD + 5 and latest > prev:
            reasons.append(f"RSI={latest:.1f} recovering from oversold")
            return 1
    except Exception as e:
        log.debug("RSI error: %s", e)
    return 0


def _bb_score(df: pd.DataFrame, reasons: list[str]) -> int:
    try:
        bb = TA.BBANDS(df, period=config.BB_PERIOD, std_multiplier=config.BB_STD)
        if bb is None or bb.empty:
            return 0
        lower = float(bb["BB_LOWER"].iloc[-1])
        price = float(df["close"].iloc[-1])
        if price <= lower * 1.002:
            reasons.append(f"Price {price:.4f} at/below BB lower {lower:.4f}")
            return 1
    except Exception as e:
        log.debug("BB error: %s", e)
    return 0


def _macd_score(df: pd.DataFrame, reasons: list[str]) -> int:
    try:
        macd = TA.MACD(df, period_fast=config.MACD_FAST,
                       period_slow=config.MACD_SLOW,
                       signal=config.MACD_SIGNAL)
        if macd is None or macd.empty:
            return 0
        hist = macd["MACD"] - macd["SIGNAL"]
        hist = hist.dropna()
        if len(hist) < 2:
            return 0
        curr, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
        if curr > 0 and prev <= 0:
            reasons.append("MACD histogram crossed above zero (bullish)")
            return 1
        if curr > prev and curr < 0:
            reasons.append("MACD histogram rising (bearish momentum fading)")
            return 1
    except Exception as e:
        log.debug("MACD error: %s", e)
    return 0


def _volume_score(df: pd.DataFrame, reasons: list[str]) -> int:
    try:
        vols = df["volume"].dropna()
        if len(vols) < config.VOLUME_AVG_PERIODS + 1:
            return 0
        avg_vol = float(vols.iloc[-config.VOLUME_AVG_PERIODS - 1:-1].mean())
        cur_vol = float(vols.iloc[-1])
        if avg_vol > 0:
            ratio = cur_vol / avg_vol
            if ratio >= config.VOLUME_SPIKE_MULTIPLIER:
                reasons.append(f"Volume spike {ratio:.1f}x average")
                return 1
    except Exception as e:
        log.debug("Volume error: %s", e)
    return 0


def _momentum_score(df: pd.DataFrame, reasons: list[str]) -> int:
    try:
        closes = df["close"].values
        if len(closes) < 5:
            return 0
        last3 = closes[-3:]
        prev2 = closes[-5:-3]
        rising_now = all(last3[i] > last3[i - 1] for i in range(1, 3))
        was_down   = prev2[-1] < prev2[0]
        if rising_now and was_down:
            reasons.append("3 consecutive up-candles after pullback")
            return 1
    except Exception as e:
        log.debug("Momentum error: %s", e)
    return 0


def analyze(symbol: str, asset_type: str, raw_bars: list[dict]) -> Optional[Opportunity]:
    df = _build_df(raw_bars)
    if df is None:
        return None

    price = float(df["close"].iloc[-1])
    if price <= 0:
        return None

    reasons: list[str] = []
    score  = 0
    score += _rsi_score(df, reasons)
    score += _bb_score(df, reasons)
    score += _macd_score(df, reasons)
    score += _volume_score(df, reasons)
    score += _momentum_score(df, reasons)

    log.debug("%s score=%d  price=%.4f", symbol, score, price)

    if score < config.MIN_SIGNAL_SCORE:
        return None

    return Opportunity(
        symbol=symbol,
        asset_type=asset_type,
        score=score,
        price=price,
        reasons=reasons,
    )
