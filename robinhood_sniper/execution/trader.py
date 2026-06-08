"""Trade executor.

Buys into a confirmed opportunity, then monitors the position on a
background thread and exits when profit target / stop / max-hold is hit.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Union

from robinhood_sniper import config
from robinhood_sniper.analysis.signals import Opportunity
from robinhood_sniper.positions.tracker import OpenTrade, PositionTracker
from robinhood_sniper.risk.manager import Decision, RiskManager

if TYPE_CHECKING:
    from robinhood_sniper.broker.robinhood_client import RobinhoodClient
    from robinhood_sniper.paper.simulator import PaperBroker

log = logging.getLogger(__name__)

Broker = Union["RobinhoodClient", "PaperBroker"]

MONITOR_INTERVAL = 5   # seconds between exit checks


class TradeExecutor:

    def __init__(
        self,
        broker:  Broker,
        tracker: PositionTracker,
        risk:    RiskManager,
    ) -> None:
        self._broker  = broker
        self._tracker = tracker
        self._risk    = risk
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="position-monitor"
        )

    def start(self) -> None:
        self._monitor_thread.start()

    # ── Entry ─────────────────────────────────────────────────────────────────

    def execute(self, opp: Opportunity) -> bool:
        """Attempt to enter a trade for the given opportunity."""
        equity = self._broker.get_portfolio_equity()
        bp     = self._broker.get_buying_power()

        dollars = min(
            bp * config.POSITION_SIZE_PCT,
            config.MAX_TRADE_DOLLARS,
        )
        dollars = max(dollars, config.MIN_TRADE_DOLLARS)

        decision, reason = self._risk.check(opp.symbol, dollars, equity)
        if decision == Decision.BLOCK:
            log.debug("BLOCKED %s: %s", opp.symbol, reason)
            return False

        log.info(
            "► ENTER  %s [%s]  score=%d  price=%.4f  $%.2f  signals=%s",
            opp.symbol, opp.asset_type, opp.score, opp.price,
            dollars, " | ".join(opp.reasons),
        )

        order_id = self._broker.buy(opp.symbol, opp.asset_type, dollars)
        if not order_id:
            log.warning("Buy order failed for %s", opp.symbol)
            return False

        # Determine exit parameters
        if opp.asset_type == "crypto":
            target_pct = config.CRYPTO_PROFIT_TARGET_PCT
            stop_pct   = config.CRYPTO_STOP_LOSS_PCT
            max_mins   = config.CRYPTO_MAX_HOLD_MINUTES
        else:
            target_pct = config.PROFIT_TARGET_PCT
            stop_pct   = config.STOP_LOSS_PCT
            max_mins   = config.MAX_HOLD_MINUTES

        entry_price    = opp.price
        profit_target  = entry_price * (1 + target_pct)
        stop_price     = entry_price * (1 - stop_pct)
        max_hold_ts    = time.time() + max_mins * 60
        quantity       = dollars / entry_price

        trade = OpenTrade(
            symbol=opp.symbol,
            asset_type=opp.asset_type,
            entry_price=entry_price,
            quantity=quantity,
            dollars=dollars,
            profit_target=profit_target,
            stop_price=stop_price,
            max_hold_ts=max_hold_ts,
            order_id=order_id,
        )
        self._tracker.add(trade)

        log.info(
            "  target=%.4f (+%.1f%%)  stop=%.4f (-%.1f%%)  max_hold=%dm",
            profit_target, target_pct * 100,
            stop_price, stop_pct * 100,
            max_mins,
        )
        return True

    # ── Exit Monitor ──────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """Background thread: checks all open trades every MONITOR_INTERVAL s."""
        log.info("Position monitor started")
        while True:
            try:
                self._check_all_exits()
            except Exception as exc:
                log.error("Monitor error: %s", exc, exc_info=True)
            time.sleep(MONITOR_INTERVAL)

    def _check_all_exits(self) -> None:
        for trade in self._tracker.all_open():
            try:
                self._check_exit(trade)
            except Exception as exc:
                log.error("Exit check error for %s: %s", trade.symbol, exc)

    def _check_exit(self, trade: OpenTrade) -> None:
        current_price = (
            self._broker.get_crypto_quote(trade.symbol)
            if trade.asset_type == "crypto"
            else self._broker.get_quote(trade.symbol)
        )
        if not current_price or current_price <= 0:
            return

        reason: str | None = None

        if current_price >= trade.profit_target:
            reason = f"profit_target ({current_price:.4f} >= {trade.profit_target:.4f})"
        elif current_price <= trade.stop_price:
            reason = f"stop_loss ({current_price:.4f} <= {trade.stop_price:.4f})"
        elif time.time() >= trade.max_hold_ts:
            reason = f"max_hold_time exceeded"

        if reason:
            log.info("◄ EXIT   %s @%.4f  [%s]", trade.symbol, current_price, reason)
            order_id = self._broker.sell(trade.symbol, trade.asset_type, trade.quantity)
            if order_id:
                self._tracker.remove(trade.symbol, current_price, reason)
                self._risk.record_cooldown(trade.symbol)
            else:
                log.warning("Sell order failed for %s — will retry next cycle", trade.symbol)
