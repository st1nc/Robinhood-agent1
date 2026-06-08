#!/usr/bin/env python3
"""
Robinhood Sniper — 24/7 trading agent
======================================
Scans stocks (regular + extended hours) and crypto (always) for high-signal
entry opportunities, fires sniper-style limit orders, and auto-exits at
predefined profit targets or stop-losses.

Usage:
    python main.py            # live or paper, as set in .env
    PAPER_TRADING=false python main.py   # override to live

Credentials and settings are read from .env (copy .env.example).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import time

# ── Bootstrap logging before importing local modules ─────────────────────────
def _setup_logging() -> None:
    from robinhood_sniper.config import LOG_LEVEL, LOG_FILE

    fmt = "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s"
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
    logging.getLogger("robin_stocks").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


_setup_logging()
log = logging.getLogger("main")

from robinhood_sniper import config
from robinhood_sniper.broker.mcp_client import MCPBroker
from robinhood_sniper.broker.robinhood_client import RobinhoodClient
from robinhood_sniper.execution.trader import TradeExecutor
from robinhood_sniper.paper.simulator import PaperBroker
from robinhood_sniper.positions.tracker import PositionTracker
from robinhood_sniper.risk.manager import RiskManager
from robinhood_sniper.scanner.opportunity import OpportunityScanner


_running = True


def _handle_signal(signum, frame) -> None:
    global _running
    log.info("Shutdown signal received — finishing current cycle…")
    _running = False


def main() -> None:
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Banner ────────────────────────────────────────────────────────────────
    mode = "PAPER" if config.PAPER_TRADING else "*** LIVE ***"
    log.info("═" * 60)
    log.info("  Robinhood Sniper Agent  |  Mode: %s", mode)
    log.info("  Max positions: %d   |  Scan every: %ds",
             config.MAX_OPEN_POSITIONS, config.SCAN_INTERVAL_SECONDS)
    log.info("  Stocks: target=+%.1f%%  stop=-%.1f%%  hold=%dm",
             config.PROFIT_TARGET_PCT * 100,
             config.STOP_LOSS_PCT * 100,
             config.MAX_HOLD_MINUTES)
    log.info("  Crypto: target=+%.1f%%  stop=-%.1f%%  hold=%dm",
             config.CRYPTO_PROFIT_TARGET_PCT * 100,
             config.CRYPTO_STOP_LOSS_PCT * 100,
             config.CRYPTO_MAX_HOLD_MINUTES)
    log.info("═" * 60)

    # ── Broker selection ──────────────────────────────────────────────────────
    # Priority: official MCP API (token present) > robin_stocks > paper
    if config.MCP_BEARER_TOKEN and not config.PAPER_TRADING:
        live_client = MCPBroker()
        logged_in   = True
        log.info("Broker: Official Robinhood MCP API  account=••••%s",
                 config.AGENTIC_ACCOUNT[-4:])
    else:
        live_client = RobinhoodClient()
        logged_in   = live_client.login()
        if not logged_in:
            if not config.PAPER_TRADING:
                log.error("Live login failed. Set PAPER_TRADING=true or fix credentials.")
                sys.exit(1)
            log.warning("Login failed but PAPER_TRADING=true — continuing in paper mode")

    broker = PaperBroker(live_client) if config.PAPER_TRADING else live_client

    # ── Components ────────────────────────────────────────────────────────────
    tracker = PositionTracker()
    risk    = RiskManager(tracker)
    scanner = OpportunityScanner(broker)
    executor = TradeExecutor(broker, tracker, risk)

    equity = broker.get_portfolio_equity()
    risk.set_start_equity(equity)
    log.info("Starting equity: $%.2f", equity)

    scanner.start()
    executor.start()

    # ── Main Loop ─────────────────────────────────────────────────────────────
    log.info("Agent running — press Ctrl+C to stop")
    heartbeat_ts = time.time()

    while _running:
        opp = scanner.get_opportunity(timeout=1.0)

        if opp is not None:
            executor.execute(opp)

        # Heartbeat log every 5 minutes
        if time.time() - heartbeat_ts >= 300:
            heartbeat_ts = time.time()
            tracker.print_summary()
            if config.PAPER_TRADING and hasattr(broker, "print_scorecard"):
                broker.print_scorecard()

        if risk.is_halted() and not config.PAPER_TRADING:
            log.warning("Risk manager halted live trading — agent sleeping until restart")
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
