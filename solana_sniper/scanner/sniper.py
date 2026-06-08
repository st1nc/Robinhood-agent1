"""Continuous new-token scanner.

Pulls fresh mints from the listing feed, runs each through the safety screen,
and pushes qualifying SnipeCandidates into a priority queue (highest safety
score pops first) for the main loop to execute.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from solana_sniper import config
from solana_sniper.analysis import filters
from solana_sniper.sources.listings import ListingFeed

log = logging.getLogger(__name__)

_QUEUE_MAX = 20


class SniperScanner:

    def __init__(self, client) -> None:
        self._client = client
        self._feed = ListingFeed()
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=_QUEUE_MAX)
        self._thread = threading.Thread(
            target=self._scan_loop, daemon=True, name="sniper-scanner"
        )
        self._last_scan_ts = 0.0

    def start(self) -> None:
        self._thread.start()
        log.info("Scanner started (interval=%ds)", config.SCAN_INTERVAL_SECONDS)

    def get_candidate(self, timeout: float = 1.0):
        """Return the highest-scored candidate, or None if the queue is empty."""
        try:
            _, cand = self._queue.get(timeout=timeout)
            return cand
        except queue.Empty:
            return None

    # ── Scan loop ─────────────────────────────────────────────────────────────

    def _scan_loop(self) -> None:
        while True:
            elapsed = time.time() - self._last_scan_ts
            if elapsed < config.SCAN_INTERVAL_SECONDS:
                time.sleep(config.SCAN_INTERVAL_SECONDS - elapsed)
            self._last_scan_ts = time.time()
            try:
                self._run_scan()
            except Exception as exc:                       # noqa: BLE001
                log.error("Scanner error: %s", exc, exc_info=True)

    def _run_scan(self) -> None:
        mints = self._feed.fresh_mints()
        if not mints:
            return

        found = 0
        for mint in mints:
            try:
                cand = filters.screen(self._client, mint)
            except Exception as exc:                       # noqa: BLE001
                log.debug("screen failed for %s: %s", mint, exc)
                continue
            if cand is None:
                continue
            found += 1
            self._enqueue(cand)
            log.info(
                "✓ CANDIDATE %s (%s)  score=%d  liq=$%.0f  age=%.1fm  [%s]",
                cand.symbol, cand.mint[:6], cand.score,
                cand.liquidity_usd, cand.pool_age_min, " | ".join(cand.reasons),
            )

        log.info("[SCAN] new_mints=%d  candidates=%d", len(mints), found)

    def _enqueue(self, cand) -> None:
        if not self._queue.full():
            self._queue.put((-cand.score, cand))
            return
        # Replace the worst queued entry if this one is better.
        try:
            neg_score, worst = self._queue.get_nowait()
            if cand.score > -neg_score:
                self._queue.put((-cand.score, cand))
            else:
                self._queue.put((neg_score, worst))
        except queue.Empty:
            self._queue.put((-cand.score, cand))
