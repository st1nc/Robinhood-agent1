"""Trade executor.

Snipes a screened candidate, then monitors the position on a background thread
and exits when the profit target, trailing stop, hard stop, or max-hold timer
fires — whichever comes first.
"""
from __future__ import annotations

import logging
import threading
import time

from solana_sniper import config
from solana_sniper.analysis.filters import SnipeCandidate
from solana_sniper.positions.tracker import OpenTrade, PositionTracker
from solana_sniper.risk.manager import Decision, RiskManager

log = logging.getLogger(__name__)

MONITOR_INTERVAL = 5   # seconds between exit checks


class TradeExecutor:

    def __init__(self, broker, tracker: PositionTracker, risk: RiskManager) -> None:
        self._broker  = broker
        self._tracker = tracker
        self._risk    = risk
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="position-monitor"
        )

    def start(self) -> None:
        self._monitor_thread.start()

    # ── Entry ─────────────────────────────────────────────────────────────────

    def execute(self, cand: SnipeCandidate) -> bool:
        balance = self._broker.get_buying_power()
        sol_amount = min(config.TRADE_SIZE_SOL, config.MAX_TRADE_SOL)

        decision, reason = self._risk.check(cand.mint, sol_amount, balance)
        if decision == Decision.BLOCK:
            log.debug("BLOCKED %s: %s", cand.symbol, reason)
            return False

        log.info(
            "► SNIPE  %s (%s)  score=%d  price=%.8f SOL  size=%.3f SOL  [%s]",
            cand.symbol, cand.mint[:6], cand.score, cand.price_native,
            sol_amount, " | ".join(cand.reasons),
        )

        order_id = self._broker.buy(cand.mint, sol_amount)
        if not order_id:
            log.warning("Buy failed for %s", cand.symbol)
            return False

        entry_price   = cand.price_native
        profit_target = entry_price * (1 + config.PROFIT_TARGET_PCT)
        stop_price    = entry_price * (1 - config.STOP_LOSS_PCT)
        max_hold_ts   = time.time() + config.MAX_HOLD_MINUTES * 60
        quantity      = sol_amount / entry_price

        trade = OpenTrade(
            mint=cand.mint,
            symbol=cand.symbol,
            entry_price=entry_price,
            quantity=quantity,
            sol_in=sol_amount,
            profit_target=profit_target,
            stop_price=stop_price,
            max_hold_ts=max_hold_ts,
            peak_price=entry_price,
            order_id=order_id,
        )
        self._tracker.add(trade)

        log.info(
            "  target=%.8f (+%.0f%%)  stop=%.8f (-%.0f%%)  trail=%.0f%%  max_hold=%.0fm",
            profit_target, config.PROFIT_TARGET_PCT * 100,
            stop_price, config.STOP_LOSS_PCT * 100,
            config.TRAILING_STOP_PCT * 100, config.MAX_HOLD_MINUTES,
        )
        return True

    # ── Exit monitor ──────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        log.info("Position monitor started")
        while True:
            try:
                for trade in self._tracker.all_open():
                    try:
                        self._check_exit(trade)
                    except Exception as exc:               # noqa: BLE001
                        log.error("Exit check error for %s: %s", trade.symbol, exc)
            except Exception as exc:                       # noqa: BLE001
                log.error("Monitor error: %s", exc, exc_info=True)
            time.sleep(MONITOR_INTERVAL)

    def _check_exit(self, trade: OpenTrade) -> None:
        price = self._broker.get_price_sol(trade.mint)
        if not price or price <= 0:
            return

        # Track peak for the trailing stop.
        if price > trade.peak_price:
            trade.peak_price = price
            self._tracker.update_peak(trade.mint, price)

        trailing_stop = trade.peak_price * (1 - config.TRAILING_STOP_PCT)
        reason: str | None = None

        if price >= trade.profit_target:
            reason = f"profit_target ({price:.8f} >= {trade.profit_target:.8f})"
        elif price <= trade.stop_price:
            reason = f"stop_loss ({price:.8f} <= {trade.stop_price:.8f})"
        elif price <= trailing_stop and trade.peak_price > trade.entry_price:
            reason = f"trailing_stop (peak={trade.peak_price:.8f}, now={price:.8f})"
        elif time.time() >= trade.max_hold_ts:
            reason = "max_hold_time exceeded"

        if not reason:
            return

        log.info("◄ EXIT   %s @%.8f SOL  [%s]", trade.symbol, price, reason)
        order_id = self._broker.sell(trade.mint, trade.quantity)
        if order_id:
            self._tracker.remove(trade.mint, price, reason)
            self._risk.record_cooldown(trade.mint)
        else:
            log.warning("Sell failed for %s — will retry next cycle", trade.symbol)
