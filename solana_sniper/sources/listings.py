"""New-token discovery feed.

Polls DexScreener's public "latest token profiles" endpoint for freshly
listed Solana mints. Each profile is just an address; the scanner enriches it
with market data and runs the safety screen. Mints already seen are remembered
so the same launch isn't surfaced twice.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from solana_sniper import config

log = logging.getLogger(__name__)

_TIMEOUT = 10


class ListingFeed:
    """Yields candidate mint addresses for newly listed Solana tokens."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._seen: set[str] = set()

    def fresh_mints(self) -> list[str]:
        """Return Solana mints not previously returned by this feed."""
        out: list[str] = []
        for mint in self._poll_profiles():
            if mint in self._seen:
                continue
            self._seen.add(mint)
            out.append(mint)

        # Keep the dedupe set from growing without bound.
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])
        return out

    def _poll_profiles(self) -> list[str]:
        url = f"{config.DEXSCREENER_BASE}/token-profiles/latest/v1"
        try:
            r = self._session.get(url, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:                          # noqa: BLE001
            log.debug("listing feed poll failed: %s", exc)
            return []

        profiles = data if isinstance(data, list) else data.get("profiles", [])
        mints: list[str] = []
        for p in profiles:
            if p.get("chainId") != config.CHAIN_ID:
                continue
            addr: Optional[str] = p.get("tokenAddress")
            if addr:
                mints.append(addr)
        return mints
