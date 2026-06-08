"""Safety screen for sniper candidates.

Sniping fresh launches is mostly a risk-management problem: most new tokens are
rugs, honeypots, or instantly dumped. This module turns on-chain + market facts
into a 0–6 safety score and a set of hard gates. A token must pass every hard
gate *and* clear ``MIN_SAFETY_SCORE`` before the executor will touch it.

Scoring (max 8):
  +1  liquidity >= MIN_LIQUIDITY_USD                     (hard gate)
  +1  liquidity / FDV >= MIN_LIQUIDITY_FDV_RATIO         (hard gate when FDV known)
  +1  5-minute volume >= MIN_VOLUME_5M_USD
  +1  buy ratio >= MIN_BUY_RATIO                          (hard gate when active)
  +1  mint authority renounced  (no infinite-mint rug)   (hard gate when required)
  +1  freeze authority renounced (no wallet freeze)       (hard gate when required)
  +1  top non-pool holder <= MAX_TOP_HOLDER_PCT           (hard gate when known)
  +1  round-trip loss <= MAX_ROUNDTRIP_LOSS_PCT           (hard gate — sellable)

Additional hard gates with no point of their own: pool-age window and the
"not already vertical" 5-minute price-change cap.
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

    # ── Pool age window (hard gate) ───────────────────────────────────────────
    created_ms = market.get("pair_created_ms") or 0
    if not created_ms:
        log.debug("%s rejected: unknown pool age", mint)
        return None
    age_sec = (time.time() * 1000 - created_ms) / 1000
    if age_sec < config.MIN_POOL_AGE_SECONDS:
        log.debug("%s skipped: pool age %.0fs < %.0fs (too fresh to verify)",
                  mint, age_sec, config.MIN_POOL_AGE_SECONDS)
        return None
    pool_age_min = age_sec / 60
    if pool_age_min > config.MAX_POOL_AGE_MINUTES:
        log.debug("%s rejected: pool age %.1fm > %.1fm",
                  mint, pool_age_min, config.MAX_POOL_AGE_MINUTES)
        return None

    # ── Not already vertical (hard gate) ──────────────────────────────────────
    pc5 = market.get("price_change_5m", 0.0)
    if pc5 > config.MAX_PRICE_CHANGE_5M_PCT:
        log.debug("%s rejected: already +%.0f%% in 5m (chasing top)", mint, pc5)
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
    reasons.append(f"liq ${liq:,.0f}")

    # ── Liquidity / FDV (hard gate + point when FDV is known) ─────────────────
    fdv = market.get("fdv") or market.get("market_cap") or 0.0
    if fdv > 0:
        ratio = liq / fdv
        if ratio < config.MIN_LIQUIDITY_FDV_RATIO:
            log.debug("%s rejected: liq/FDV %.1f%% < %.1f%% (thin pool, huge cap)",
                      mint, ratio * 100, config.MIN_LIQUIDITY_FDV_RATIO * 100)
            return None
        score += 1
        reasons.append(f"liq/fdv {ratio*100:.0f}%")

    # ── Volume ────────────────────────────────────────────────────────────────
    vol5 = market["volume_5m_usd"]
    if vol5 >= config.MIN_VOLUME_5M_USD:
        score += 1
        reasons.append(f"5m vol ${vol5:,.0f}")

    # ── Buy pressure (hard gate + point when there's enough activity) ─────────
    buys, sells = market.get("buys_5m", 0), market.get("sells_5m", 0)
    total_tx = buys + sells
    if total_tx >= 5:
        buy_ratio = buys / total_tx
        if buy_ratio < config.MIN_BUY_RATIO:
            log.debug("%s rejected: buy ratio %.0f%% < %.0f%% (being dumped)",
                      mint, buy_ratio * 100, config.MIN_BUY_RATIO * 100)
            return None
        score += 1
        reasons.append(f"buys {buy_ratio*100:.0f}%")

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

    # ── Holder concentration (hard gate when known) ───────────────────────────
    top_pct = client.get_top_holder_pct(mint)
    if top_pct is not None:
        if top_pct > config.MAX_TOP_HOLDER_PCT:
            log.debug("%s rejected: top holder %.0f%% > %.0f%% (dump risk)",
                      mint, top_pct, config.MAX_TOP_HOLDER_PCT)
            return None
        score += 1
        reasons.append(f"top holder {top_pct:.0f}%")

    # ── Honeypot / tax check (hard gate, sized to the real trade) ─────────────
    loss = client.roundtrip_loss_pct(mint, config.TRADE_SIZE_SOL)
    if loss is None:
        log.debug("%s rejected: no sell route (possible honeypot)", mint)
        return None
    if loss > config.MAX_ROUNDTRIP_LOSS_PCT:
        log.debug("%s rejected: round-trip loss %.0f%% (high tax/honeypot)",
                  mint, loss * 100)
        return None
    score += 1
    reasons.append(f"rt loss {loss*100:.0f}%")

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
