"""Paper-trading broker that mirrors the live RobinhoodClient interface.

All fills are at the last known price with a configurable slippage.
Virtual P&L and trade log are kept in memory and printed on demand.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from robinhood_sniper import config

log = logging.getLogger(__name__)

SLIPPAGE = 0.001   # 0.1 % slippage on every fill


@dataclass
class PaperOrder:
    order_id:   str
    symbol:     str
    asset_type: str         # "stock" | "crypto"
    side:       str         # "buy" | "sell"
    quantity:   float
    fill_price: float
    timestamp:  float = field(default_factory=time.time)
    state:      str = "filled"


class PaperBroker:
    """Simulates order execution using real-time quotes from the live broker."""

    def __init__(self, live_client) -> None:
        self._live     = live_client
        self._lock     = threading.Lock()
        self._balance  = config.PAPER_BALANCE
        self._equity   = config.PAPER_BALANCE  # updated on quote
        self._orders:  list[PaperOrder] = []
        self._positions: dict[str, dict] = {}   # symbol → {qty, avg_cost, type}
        self._daily_pnl    = 0.0
        self._daily_trades = 0

    # ── Account ───────────────────────────────────────────────────────────────

    def get_buying_power(self) -> float:
        return self._balance

    def get_portfolio_equity(self) -> float:
        return self._equity

    def get_daily_pnl(self) -> float:
        return self._daily_pnl

    def get_daily_trades(self) -> int:
        return self._daily_trades

    # ── Quotes (delegated to live) ────────────────────────────────────────────

    def get_quote(self, symbol: str) -> Optional[float]:
        return self._live.get_quote(symbol)

    def get_crypto_quote(self, symbol: str) -> Optional[float]:
        return self._live.get_crypto_quote(symbol)

    def get_historicals(self, symbol: str, asset_type: str) -> list[dict]:
        return self._live.get_historicals(symbol, asset_type)

    # ── Orders ────────────────────────────────────────────────────────────────

    def buy(
        self,
        symbol:     str,
        asset_type: str,
        dollars:    float,
    ) -> Optional[str]:
        price = (
            self.get_crypto_quote(symbol)
            if asset_type == "crypto"
            else self.get_quote(symbol)
        )
        if not price or price <= 0:
            log.warning("[PAPER] Could not get quote for %s", symbol)
            return None

        fill_price = price * (1 + SLIPPAGE)
        quantity   = dollars / fill_price

        with self._lock:
            if self._balance < dollars:
                log.warning("[PAPER] Insufficient balance for %s (need $%.2f)", symbol, dollars)
                return None

            self._balance -= dollars
            pos = self._positions.get(symbol)
            if pos:
                total_qty  = pos["qty"] + quantity
                total_cost = pos["qty"] * pos["avg_cost"] + quantity * fill_price
                pos["qty"]      = total_qty
                pos["avg_cost"] = total_cost / total_qty
            else:
                self._positions[symbol] = {
                    "qty": quantity,
                    "avg_cost": fill_price,
                    "type": asset_type,
                }

            order_id = str(uuid.uuid4())[:8]
            self._orders.append(PaperOrder(
                order_id=order_id, symbol=symbol, asset_type=asset_type,
                side="buy", quantity=quantity, fill_price=fill_price,
            ))
            self._daily_trades += 1

        log.info(
            "[PAPER] BUY  %s  qty=%.6f  @$%.4f  cost=$%.2f  balance=$%.2f",
            symbol, quantity, fill_price, dollars, self._balance,
        )
        return order_id

    def sell(
        self,
        symbol:     str,
        asset_type: str,
        quantity:   Optional[float] = None,
    ) -> Optional[str]:
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos["qty"] <= 0:
                log.warning("[PAPER] No position in %s to sell", symbol)
                return None

            sell_qty = quantity if quantity else pos["qty"]
            sell_qty = min(sell_qty, pos["qty"])

            price = (
                self.get_crypto_quote(symbol)
                if asset_type == "crypto"
                else self.get_quote(symbol)
            )
            if not price or price <= 0:
                log.warning("[PAPER] Could not get exit quote for %s", symbol)
                return None

            fill_price = price * (1 - SLIPPAGE)
            proceeds   = sell_qty * fill_price
            cost_basis = sell_qty * pos["avg_cost"]
            pnl        = proceeds - cost_basis

            self._balance      += proceeds
            self._daily_pnl    += pnl
            pos["qty"]         -= sell_qty

            if pos["qty"] <= 1e-8:
                del self._positions[symbol]

            order_id = str(uuid.uuid4())[:8]
            self._orders.append(PaperOrder(
                order_id=order_id, symbol=symbol, asset_type=asset_type,
                side="sell", quantity=sell_qty, fill_price=fill_price,
            ))
            self._daily_trades += 1

        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
        log.info(
            "[PAPER] SELL %s  qty=%.6f  @$%.4f  pnl=$%.2f (%.2f%%)  balance=$%.2f",
            symbol, sell_qty, fill_price, pnl, pnl_pct, self._balance,
        )
        return order_id

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_open_positions(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._positions)

    def get_position(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._positions.get(symbol)

    # ── Scorecard ─────────────────────────────────────────────────────────────

    def print_scorecard(self) -> None:
        with self._lock:
            sells = [o for o in self._orders if o.side == "sell"]
            wins  = sum(1 for o in sells if o.fill_price > 0)   # proxy
            print("\n═══════════  PAPER TRADING SCORECARD  ═══════════")
            print(f"  Balance:       ${self._balance:>10.2f}")
            print(f"  Daily P&L:     ${self._daily_pnl:>+10.2f}")
            print(f"  Total trades:  {self._daily_trades}")
            print(f"  Open pos.:     {len(self._positions)}")
            print("══════════════════════════════════════════════════\n")
