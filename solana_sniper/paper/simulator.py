"""Paper-trading broker mirroring the live SolanaClient interface.

Market data (pricing, mint authorities, holder concentration, route checks) is
delegated to the live client so screening behaves identically. Only order
execution is simulated: fills happen at the live price plus slippage, and a
virtual SOL balance is tracked in memory. This is the default mode.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from solana_sniper import config

log = logging.getLogger(__name__)

SLIPPAGE = config.SLIPPAGE_BPS / 10_000   # model swap slippage on every fill


@dataclass
class PaperOrder:
    order_id:   str
    mint:       str
    side:       str          # "buy" | "sell"
    quantity:   float
    fill_price: float        # SOL per token
    timestamp:  float = field(default_factory=time.time)


class PaperBroker:
    """Simulates swap execution using live on-chain prices."""

    def __init__(self, live_client) -> None:
        self._live    = live_client
        self._lock    = threading.Lock()
        self._balance = config.PAPER_BALANCE_SOL
        self._orders: list[PaperOrder] = []
        self._positions: dict[str, dict] = {}   # mint → {qty, avg_cost}
        self._daily_pnl = 0.0
        self._daily_trades = 0

    # ── Account ───────────────────────────────────────────────────────────────

    def login(self) -> bool:
        return True

    def logout(self) -> None:
        pass

    def get_buying_power(self) -> float:
        return self._balance

    def get_portfolio_equity(self) -> float:
        return self._balance

    # ── Market data (delegated to live client) ────────────────────────────────

    def get_token_market(self, mint: str):
        return self._live.get_token_market(mint)

    def get_price_sol(self, mint: str):
        return self._live.get_price_sol(mint)

    def get_mint_authorities(self, mint: str):
        return self._live.get_mint_authorities(mint)

    def get_top_holder_pct(self, mint: str):
        return self._live.get_top_holder_pct(mint)

    def get_decimals(self, mint: str):
        return self._live.get_decimals(mint)

    def roundtrip_loss_pct(self, mint: str, sol_amount: float):
        return self._live.roundtrip_loss_pct(mint, sol_amount)

    # ── Orders ────────────────────────────────────────────────────────────────

    def buy(self, mint: str, sol_amount: float) -> Optional[str]:
        price = self._live.get_price_sol(mint)
        if not price or price <= 0:
            log.warning("[PAPER] No quote for %s", mint[:6])
            return None

        fill_price = price * (1 + SLIPPAGE)
        quantity   = sol_amount / fill_price

        with self._lock:
            if self._balance < sol_amount:
                log.warning("[PAPER] Insufficient balance for %s", mint[:6])
                return None
            self._balance -= sol_amount
            pos = self._positions.get(mint)
            if pos:
                total_qty  = pos["qty"] + quantity
                total_cost = pos["qty"] * pos["avg_cost"] + quantity * fill_price
                pos["qty"], pos["avg_cost"] = total_qty, total_cost / total_qty
            else:
                self._positions[mint] = {"qty": quantity, "avg_cost": fill_price}

            order_id = str(uuid.uuid4())[:8]
            self._orders.append(PaperOrder(order_id, mint, "buy", quantity, fill_price))
            self._daily_trades += 1

        log.info(
            "[PAPER] BUY  %s  qty=%.2f  @%.8f SOL  spent=%.4f  balance=%.4f",
            mint[:6], quantity, fill_price, sol_amount, self._balance,
        )
        return order_id

    def sell(self, mint: str, quantity: Optional[float] = None) -> Optional[str]:
        with self._lock:
            pos = self._positions.get(mint)
            if not pos or pos["qty"] <= 0:
                log.warning("[PAPER] No position in %s to sell", mint[:6])
                return None

            sell_qty = min(quantity or pos["qty"], pos["qty"])

        price = self._live.get_price_sol(mint)
        if not price or price <= 0:
            log.warning("[PAPER] No exit quote for %s", mint[:6])
            return None

        with self._lock:
            pos = self._positions.get(mint)
            if not pos:
                return None
            fill_price = price * (1 - SLIPPAGE)
            proceeds   = sell_qty * fill_price
            cost_basis = sell_qty * pos["avg_cost"]
            pnl        = proceeds - cost_basis

            self._balance   += proceeds
            self._daily_pnl += pnl
            pos["qty"]      -= sell_qty
            if pos["qty"] <= 1e-9:
                del self._positions[mint]

            order_id = str(uuid.uuid4())[:8]
            self._orders.append(PaperOrder(order_id, mint, "sell", sell_qty, fill_price))
            self._daily_trades += 1

        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
        log.info(
            "[PAPER] SELL %s  qty=%.2f  @%.8f SOL  pnl=%.4f (%.1f%%)  balance=%.4f",
            mint[:6], sell_qty, fill_price, pnl, pnl_pct, self._balance,
        )
        return order_id

    # ── Scorecard ─────────────────────────────────────────────────────────────

    def print_scorecard(self) -> None:
        with self._lock:
            print("\n═══════════  PAPER SNIPER SCORECARD  ═══════════")
            print(f"  Balance:       {self._balance:>10.4f} SOL")
            print(f"  Daily P&L:     {self._daily_pnl:>+10.4f} SOL")
            print(f"  Total trades:  {self._daily_trades}")
            print(f"  Open pos.:     {len(self._positions)}")
            print("════════════════════════════════════════════════\n")
