#!/usr/bin/env python3
"""
Solana Sniper — on-chain new-token sniping agent
================================================
Continuously discovers freshly-launched Solana tokens, screens them for
rug/honeypot risk (liquidity, renounced mint & freeze authority, holder
concentration, sellability), snipes qualifying entries through the Jupiter
aggregator, and auto-exits at profit targets, trailing stops, or stop-losses.

Usage:
    python solana_main.py                      # paper or live, per .env
    PAPER_TRADING=false python solana_main.py  # override to live

Configuration and the wallet key are read from .env (copy .env.example).
"""
from __future__ import annotations

import logging
import logging.handlers
import signal
import sys
import time


def _setup_logging() -> None:
    from solana_sniper.config import LOG_LEVEL, LOG_FILE

    fmt = "%(asctime)s  %(levelname)-8s  %(name)-32s  %(message)s"
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
            )
        )
    except OSError:
        pass

    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


_setup_logging()
log = logging.getLogger("main")

from solana_sniper import config
from solana_sniper.chain.client import SolanaClient
from solana_sniper.execution.trader import TradeExecutor
from solana_sniper.paper.simulator import PaperBroker
from solana_sniper.positions.tracker import PositionTracker
from solana_sniper.risk.manager import RiskManager
from solana_sniper.scanner.sniper import SniperScanner


_running = True


def _handle_signal(signum, frame) -> None:
    global _running
    log.info("Shutdown signal received — finishing current cycle…")
    _running = False


def main() -> None:
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    mode = "PAPER" if config.PAPER_TRADING else "*** LIVE ***"
    log.info("═" * 64)
    log.info("  Solana Sniper Agent  |  Mode: %s", mode)
    log.info("  Trade size: %.3f SOL   |  Max positions: %d   |  Scan: %ds",
             config.TRADE_SIZE_SOL, config.MAX_OPEN_POSITIONS,
             config.SCAN_INTERVAL_SECONDS)
    log.info("  Exits: target=+%.0f%%  stop=-%.0f%%  trail=%.0f%%  hold=%.0fm",
             config.PROFIT_TARGET_PCT * 100, config.STOP_LOSS_PCT * 100,
             config.TRAILING_STOP_PCT * 100, config.MAX_HOLD_MINUTES)
    log.info("  Filters: min_liq=$%.0f  min_score=%d  pool_age<=%.0fm",
             config.MIN_LIQUIDITY_USD, config.MIN_SAFETY_SCORE,
             config.MAX_POOL_AGE_MINUTES)
    log.info("═" * 64)

    # ── Broker ────────────────────────────────────────────────────────────────
    live_client = SolanaClient()
    logged_in   = live_client.login()

    if not logged_in and not config.PAPER_TRADING:
        log.error("Wallet login failed. Set PAPER_TRADING=true or fix WALLET_PRIVATE_KEY.")
        sys.exit(1)
    if not logged_in:
        log.warning("Wallet not loaded but PAPER_TRADING=true — using public market data")

    broker = PaperBroker(live_client) if config.PAPER_TRADING else live_client

    # ── Components ────────────────────────────────────────────────────────────
    tracker  = PositionTracker()
    risk     = RiskManager(tracker)
    scanner  = SniperScanner(live_client)   # screening always uses live market data
    executor = TradeExecutor(broker, tracker, risk)

    balance = broker.get_portfolio_equity()
    log.info("Starting balance: %.4f SOL", balance)

    scanner.start()
    executor.start()

    log.info("Agent running — press Ctrl+C to stop")
    heartbeat_ts = time.time()

    while _running:
        cand = scanner.get_candidate(timeout=1.0)
        if cand is not None:
            executor.execute(cand)

        if time.time() - heartbeat_ts >= 300:
            heartbeat_ts = time.time()
            tracker.print_summary()
            if config.PAPER_TRADING and hasattr(broker, "print_scorecard"):
                broker.print_scorecard()

        if risk.is_halted() and not config.PAPER_TRADING:
            log.warning("Risk manager halted live trading — sleeping until restart")
            while _running:
                time.sleep(10)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Shutting down…")
    tracker.print_summary()
    if config.PAPER_TRADING and hasattr(broker, "print_scorecard"):
        broker.print_scorecard()
    if logged_in and not config.PAPER_TRADING:
        live_client.logout()
    log.info("Agent stopped.")


if __name__ == "__main__":
    main()
