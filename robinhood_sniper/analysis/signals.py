"""Technical-analysis signal engine.

Consumes an OHLCV DataFrame and returns a scored Opportunity.
All trades are LONG-only (Robinhood does not support shorting stocks/crypto).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

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
                "volume": float(bar.get("volume", 0)),
            })
        except (TypeError, ValueError):
            continue

    if len(rows) < config.BB_PERIOD + 5:
        return None

    df = pd.DataFrame(rows)
    df.dropna(inplace=True)
    return df


def _rsi_score(df: pd.DataFrame, reasons: list[str]) -> int:
    """RSI oversold entry signal (+1 or +2)."""
    rsi_series = ta.rsi(df["close"], length=config.RSI_PERIOD)
    if rsi_series is None or rsi_series.dropna().empty:
        return 0

    rsi_latest = rsi_series.iloc[-1]
    rsi_prev   = rsi_series.iloc[-2] if len(rsi_series) > 1 else rsi_latest

    if rsi_latest < config.RSI_OVERSOLD:
        reasons.append(f"RSI={rsi_latest:.1f} deeply oversold")
        return 2
    if rsi_prev < config.RSI_OVERSOLD + 5 and rsi_latest > rsi_prev:
        reasons.append(f"RSI={rsi_latest:.1f} recovering from oversold")
        return 1
    return 0


def _bb_score(df: pd.DataFrame, reasons: list[str]) -> int:
    """Price near/below lower Bollinger Band (+1)."""
    bb = ta.bbands(df["close"], length=config.BB_PERIOD, std=config.BB_STD)
    if bb is None or bb.empty:
        return 0

    lower_col = [c for c in bb.columns if "BBL" in c]
    upper_col = [c for c in bb.columns if "BBU" in c]
    mid_col   = [c for c in bb.columns if "BBM" in c]

    if not lower_col:
        return 0

    lower  = bb[lower_col[0]].iloc[-1]
    price  = df["close"].iloc[-1]

    if price <= lower * 1.002:   # at or just above lower band
        reasons.append(f"Price {price:.4f} at/below BB lower {lower:.4f}")
        return 1
    return 0


def _macd_score(df: pd.DataFrame, reasons: list[str]) -> int:
    """MACD histogram turning bullish (+1)."""
    macd = ta.macd(
        df["close"],
        fast=config.MACD_FAST,
        slow=config.MACD_SLOW,
        signal=config.MACD_SIGNAL,
    )
    if macd is None or macd.empty:
        return 0

    hist_col = [c for c in macd.columns if "MACDh" in c]
    if not hist_col:
        return 0

    hist     = macd[hist_col[0]]
    if len(hist.dropna()) < 2:
        return 0

    curr = hist.iloc[-1]
    prev = hist.iloc[-2]

    if curr > 0 and prev <= 0:
        reasons.append("MACD histogram crossed above zero (bullish)")
        return 1
    if curr > prev and curr < 0:
        reasons.append("MACD histogram rising (bearish momentum fading)")
        return 1
    return 0


def _volume_score(df: pd.DataFrame, reasons: list[str]) -> int:
    """Volume spike vs rolling average (+1)."""
    if df["volume"].iloc[-1] == 0:
        return 0

    avg_vol = df["volume"].iloc[-config.VOLUME_AVG_PERIODS - 1 : -1].mean()
    if avg_vol <= 0:
        return 0

    ratio = df["volume"].iloc[-1] / avg_vol
    if ratio >= config.VOLUME_SPIKE_MULTIPLIER:
        reasons.append(f"Volume spike {ratio:.1f}x average")
        return 1
    return 0


def _momentum_score(df: pd.DataFrame, reasons: list[str]) -> int:
    """3 consecutive rising closes after a down period (+1)."""
    closes = df["close"].values
    if len(closes) < 5:
        return 0

    last3 = closes[-3:]
    prev2 = closes[-5:-3]

    rising_now = all(last3[i] > last3[i - 1] for i in range(1, 3))
    was_down   = prev2[-1] < prev2[0]

    if rising_now and was_down:
        reasons.append("3 consecutive up-candles after a pullback")
        return 1
    return 0


def analyze(symbol: str, asset_type: str, raw_bars: list[dict]) -> Optional[Opportunity]:
    """Run all signal checks and return an Opportunity if score >= threshold."""
    df = _build_df(raw_bars)
    if df is None:
        return None

    price = df["close"].iloc[-1]
    if price <= 0:
        return None

    reasons: list[str] = []
    score = 0
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
