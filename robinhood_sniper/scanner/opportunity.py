"""Continuous market scanner.

Iterates over the full watchlist, fetches OHLCV bars, runs technical analysis,
and pushes ranked Opportunity objects into a priority queue for the main loop.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Union

from robinhood_sniper import config
from robinhood_sniper.analysis import signals as sig
from robinhood_sniper.market import hours

log = logging.getLogger(__name__)

Broker = Union[
    "robinhood_sniper.broker.robinhood_client.RobinhoodClient",  # type: ignore[name-defined]
    "robinhood_sniper.paper.simulator.PaperBroker",              # type: ignore[name-defined]
]

# Max opportunities queued at once; oldest are dropped when full
_QUEUE_MAX = 20


class OpportunityScanner:

    def __init__(self, broker) -> None:
        self._broker = broker
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=_QUEUE_MAX)
        self._thread = threading.Thread(
            target=self._scan_loop, daemon=True, name="scanner"
        )
        self._last_scan_ts = 0.0

    def start(self) -> None:
        self._thread.start()
        log.info("Scanner started (interval=%ds)", config.SCAN_INTERVAL_SECONDS)

    def get_opportunity(self, timeout: float = 1.0):
        """Return the highest-scored Opportunity or None if queue is empty."""
        try:
            # PriorityQueue is a min-heap; we store (-score, opp) so highest score pops first
            _, opp = self._queue.get(timeout=timeout)
            return opp
        except queue.Empty:
            return None

    # ── Scan Loop ─────────────────────────────────────────────────────────────

    def _scan_loop(self) -> None:
        while True:
            elapsed = time.time() - self._last_scan_ts
            if elapsed < config.SCAN_INTERVAL_SECONDS:
                time.sleep(config.SCAN_INTERVAL_SECONDS - elapsed)

            self._last_scan_ts = time.time()
            try:
                self._run_scan()
            except Exception as exc:
                log.error("Scanner error: %s", exc, exc_info=True)

    def _run_scan(self) -> None:
        symbols: list[tuple[str, str]] = []

        if hours.crypto_available():
            symbols += [(s, "crypto") for s in config.CRYPTO_WATCHLIST]

        if hours.stocks_available():
            symbols += [(s, "stock") for s in config.STOCK_WATCHLIST]

        if not symbols:
            log.debug("No markets available — sleeping")
            return

        found = 0
        for symbol, asset_type in symbols:
            opp = self._analyze_symbol(symbol, asset_type)
            if opp is None:
                continue
            found += 1
            if not self._queue.full():
                self._queue.put((-opp.score, opp))
            else:
                # Try to replace lowest-score entry if this one is better
                try:
                    neg_score, worst = self._queue.get_nowait()
                    if opp.score > -neg_score:
                        self._queue.put((-opp.score, opp))
                    else:
                        self._queue.put((neg_score, worst))
                except queue.Empty:
                    self._queue.put((-opp.score, opp))

        session = hours.current_session().name
        log.info(
            "[SCAN] session=%-12s  scanned=%d  opportunities=%d",
            session, len(symbols), found,
        )

    def _analyze_symbol(self, symbol: str, asset_type: str):
        try:
            bars = self._broker.get_historicals(symbol, asset_type)
            return sig.analyze(symbol, asset_type, bars)
        except Exception as exc:
            log.debug("Analysis failed for %s: %s", symbol, exc)
            return None
