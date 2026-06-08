"""Safety screen for sniper candidates.

Sniping fresh launches is mostly a risk-management problem: most new tokens are
rugs, honeypots, or instantly dumped. This module turns on-chain + market facts
into a 0–6 safety score and a set of hard gates. A token must pass every hard
gate *and* clear ``MIN_SAFETY_SCORE`` before the executor will touch it.

Scoring (max 6):
  +1  liquidity >= MIN_LIQUIDITY_USD
  +1  5-minute volume >= MIN_VOLUME_5M_USD
  +1  mint authority renounced  (no infinite-mint rug)
  +1  freeze authority renounced (tokens can't be frozen in your wallet)
  +1  top holder below MAX_TOP_HOLDER_PCT (not a single-whale token)
  +1  round-trip loss below MAX_ROUNDTRIP_LOSS_PCT (sellable, low tax)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from solana_sniper import config

log = logging.getLogger(__name__)


@dataclass
class SnipeCandidate:
    mint:          str
    symbol:        str
    name:          str
    price_native:  float          # SOL per token (entry reference)
    price_usd:     float
    liquidity_usd: float
    volume_5m_usd: float
    pool_age_min:  float
    score:         int
    reasons:       list[str] = field(default_factory=list)

    def __lt__(self, other: "SnipeCandidate") -> bool:
        return self.score < other.score


def screen(client, mint: str) -> Optional[SnipeCandidate]:
    """Run the full safety screen. Returns a candidate or None if rejected."""
    market = client.get_token_market(mint)
    if not market or not market.get("price_native"):
        return None

    if config.ALLOWED_DEXES and market["dex_id"] not in config.ALLOWED_DEXES:
        log.debug("%s rejected: dex %s not allowed", mint, market["dex_id"])
        return None

    # ── Pool age (hard gate) ──────────────────────────────────────────────────
    created_ms = market.get("pair_created_ms") or 0
    pool_age_min = (time.time() * 1000 - created_ms) / 60_000 if created_ms else 1e9
    if pool_age_min > config.MAX_POOL_AGE_MINUTES:
        log.debug("%s rejected: pool age %.1fm > %.1fm",
                  mint, pool_age_min, config.MAX_POOL_AGE_MINUTES)
        return None

    reasons: list[str] = []
    score = 0

    # ── Liquidity (hard gate + point) ─────────────────────────────────────────
    liq = market["liquidity_usd"]
    if liq < config.MIN_LIQUIDITY_USD:
        log.debug("%s rejected: liquidity $%.0f < $%.0f",
                  mint, liq, config.MIN_LIQUIDITY_USD)
        return None
    score += 1
    reasons.append(f"liquidity ${liq:,.0f}")

    # ── Volume ────────────────────────────────────────────────────────────────
    vol5 = market["volume_5m_usd"]
    if vol5 >= config.MIN_VOLUME_5M_USD:
        score += 1
        reasons.append(f"5m vol ${vol5:,.0f}")

    # ── Mint / freeze authority (hard gates if required) ──────────────────────
    auth = client.get_mint_authorities(mint)
    mint_renounced   = auth.get("mint_renounced")
    freeze_renounced = auth.get("freeze_renounced")

    if config.REQUIRE_MINT_RENOUNCED and mint_renounced is False:
        log.debug("%s rejected: mint authority still active", mint)
        return None
    if config.REQUIRE_FREEZE_RENOUNCED and freeze_renounced is False:
        log.debug("%s rejected: freeze authority still active", mint)
        return None
    if mint_renounced:
        score += 1
        reasons.append("mint renounced")
    if freeze_renounced:
        score += 1
        reasons.append("freeze renounced")

    # ── Holder concentration ──────────────────────────────────────────────────
    top_pct = client.get_top_holder_pct(mint)
    if top_pct is not None:
        if top_pct <= config.MAX_TOP_HOLDER_PCT:
            score += 1
            reasons.append(f"top holder {top_pct:.0f}%")
        else:
            log.debug("%s top holder %.0f%% > %.0f%%",
                      mint, top_pct, config.MAX_TOP_HOLDER_PCT)

    # ── Honeypot / tax check (hard gate) ──────────────────────────────────────
    loss = client.roundtrip_loss_pct(mint, config.MIN_TRADE_SOL)
    if loss is None:
        log.debug("%s rejected: no sell route (possible honeypot)", mint)
        return None
    if loss > config.MAX_ROUNDTRIP_LOSS_PCT:
        log.debug("%s rejected: round-trip loss %.0f%% (high tax/honeypot)",
                  mint, loss * 100)
        return None
    score += 1
    reasons.append(f"round-trip loss {loss*100:.0f}%")

    if score < config.MIN_SAFETY_SCORE:
        log.debug("%s score %d < %d", mint, score, config.MIN_SAFETY_SCORE)
        return None

    return SnipeCandidate(
        mint=mint,
        symbol=market["symbol"],
        name=market["name"],
        price_native=market["price_native"],
        price_usd=market["price_usd"] or 0.0,
        liquidity_usd=liq,
        volume_5m_usd=vol5,
        pool_age_min=pool_age_min,
        score=score,
        reasons=reasons,
    )
