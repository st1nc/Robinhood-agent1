"""SQLite-backed position tracker.

Persists every open snipe (entry price, target, stop, peak, max-hold) keyed by
mint address so the agent recovers its open positions across restarts. All
prices and P&L are denominated in SOL.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from solana_sniper import config

log = logging.getLogger(__name__)


@dataclass
class OpenTrade:
    mint:          str
    symbol:        str
    entry_price:   float          # SOL per token
    quantity:      float          # tokens held
    sol_in:        float          # SOL spent
    profit_target: float          # absolute price (SOL)
    stop_price:    float          # absolute price (SOL)
    max_hold_ts:   float          # unix ts to force-close
    peak_price:    float          # highest seen price (for trailing stop)
    order_id:      str = ""
    entry_ts:      float = field(default_factory=time.time)


class PositionTracker:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        self._create_tables()
        self._memory: dict[str, OpenTrade] = {}
        self._load_from_db()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS open_trades (
                mint          TEXT PRIMARY KEY,
                symbol        TEXT,
                entry_price   REAL,
                quantity      REAL,
                sol_in        REAL,
                profit_target REAL,
                stop_price    REAL,
                max_hold_ts   REAL,
                peak_price    REAL,
                order_id      TEXT,
                entry_ts      REAL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                mint          TEXT,
                symbol        TEXT,
                entry_price   REAL,
                exit_price    REAL,
                quantity      REAL,
                pnl_sol       REAL,
                pnl_pct       REAL,
                reason        TEXT,
                entry_ts      REAL,
                exit_ts       REAL
            )
        """)
        self._conn.commit()

    def _load_from_db(self) -> None:
        cur = self._conn.execute("SELECT * FROM open_trades")
        for row in cur.fetchall():
            t = OpenTrade(*row)
            self._memory[t.mint] = t
        if self._memory:
            log.info("Loaded %d open trade(s) from DB", len(self._memory))

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(self, trade: OpenTrade) -> None:
        with self._lock:
            self._memory[trade.mint] = trade
            self._conn.execute(
                "INSERT OR REPLACE INTO open_trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trade.mint, trade.symbol, trade.entry_price, trade.quantity,
                    trade.sol_in, trade.profit_target, trade.stop_price,
                    trade.max_hold_ts, trade.peak_price, trade.order_id,
                    trade.entry_ts,
                ),
            )
            self._conn.commit()

    def update_peak(self, mint: str, peak_price: float) -> None:
        with self._lock:
            trade = self._memory.get(mint)
            if not trade:
                return
            trade.peak_price = peak_price
            self._conn.execute(
                "UPDATE open_trades SET peak_price = ? WHERE mint = ?",
                (peak_price, mint),
            )
            self._conn.commit()

    def remove(self, mint: str, exit_price: float, reason: str) -> Optional[OpenTrade]:
        with self._lock:
            trade = self._memory.pop(mint, None)
            if not trade:
                return None

            pnl     = (exit_price - trade.entry_price) * trade.quantity
            pnl_pct = (exit_price / trade.entry_price - 1) * 100 if trade.entry_price else 0

            self._conn.execute("DELETE FROM open_trades WHERE mint = ?", (mint,))
            self._conn.execute("""
                INSERT INTO closed_trades
                (mint, symbol, entry_price, exit_price, quantity,
                 pnl_sol, pnl_pct, reason, entry_ts, exit_ts)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                mint, trade.symbol, trade.entry_price, exit_price,
                trade.quantity, pnl, pnl_pct, reason,
                trade.entry_ts, time.time(),
            ))
            self._conn.commit()

            flag = "✓" if pnl >= 0 else "✗"
            log.info(
                "%s CLOSED %s  entry=%.8f exit=%.8f  pnl=%.4f SOL (%.1f%%)  [%s]",
                flag, trade.symbol, trade.entry_price, exit_price, pnl, pnl_pct, reason,
            )
            return trade

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, mint: str) -> Optional[OpenTrade]:
        with self._lock:
            return self._memory.get(mint)

    def all_open(self) -> list[OpenTrade]:
        with self._lock:
            return list(self._memory.values())

    def count(self) -> int:
        with self._lock:
            return len(self._memory)

    def has(self, mint: str) -> bool:
        with self._lock:
            return mint in self._memory

    # ── Stats ─────────────────────────────────────────────────────────────────

    def daily_pnl(self) -> float:
        cur = self._conn.execute(
            "SELECT SUM(pnl_sol) FROM closed_trades WHERE exit_ts > ?",
            (time.time() - 86_400,),
        )
        return float(cur.fetchone()[0] or 0)

    def daily_trade_count(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM closed_trades WHERE exit_ts > ?",
            (time.time() - 86_400,),
        )
        return int(cur.fetchone()[0] or 0)

    def print_summary(self) -> None:
        log.info(
            "── SUMMARY  daily_pnl=%.4f SOL  closed=%d  open=%d ──",
            self.daily_pnl(), self.daily_trade_count(), self.count(),
        )
