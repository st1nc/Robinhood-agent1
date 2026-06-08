"""SQLite-backed position tracker.

Stores every open trade with its entry price, target, stop, and max-hold time
so the agent survives restarts without losing track of live positions.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from robinhood_sniper import config

log = logging.getLogger(__name__)


@dataclass
class OpenTrade:
    symbol:        str
    asset_type:    str       # "stock" | "crypto"
    entry_price:   float
    quantity:      float
    dollars:       float
    profit_target: float     # absolute price
    stop_price:    float     # absolute price
    max_hold_ts:   float     # unix timestamp when position must be closed
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
                symbol        TEXT PRIMARY KEY,
                asset_type    TEXT,
                entry_price   REAL,
                quantity      REAL,
                dollars       REAL,
                profit_target REAL,
                stop_price    REAL,
                max_hold_ts   REAL,
                order_id      TEXT,
                entry_ts      REAL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT,
                asset_type    TEXT,
                entry_price   REAL,
                exit_price    REAL,
                quantity      REAL,
                pnl           REAL,
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
            self._memory[t.symbol] = t
        if self._memory:
            log.info("Loaded %d open trade(s) from DB", len(self._memory))

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(self, trade: OpenTrade) -> None:
        with self._lock:
            self._memory[trade.symbol] = trade
            self._conn.execute("""
                INSERT OR REPLACE INTO open_trades
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                trade.symbol, trade.asset_type, trade.entry_price,
                trade.quantity, trade.dollars, trade.profit_target,
                trade.stop_price, trade.max_hold_ts, trade.order_id,
                trade.entry_ts,
            ))
            self._conn.commit()

    def remove(
        self,
        symbol:     str,
        exit_price: float,
        reason:     str,
    ) -> Optional[OpenTrade]:
        with self._lock:
            trade = self._memory.pop(symbol, None)
            if not trade:
                return None

            pnl     = (exit_price - trade.entry_price) * trade.quantity
            pnl_pct = (exit_price / trade.entry_price - 1) * 100

            self._conn.execute("DELETE FROM open_trades WHERE symbol = ?", (symbol,))
            self._conn.execute("""
                INSERT INTO closed_trades
                (symbol, asset_type, entry_price, exit_price, quantity,
                 pnl, pnl_pct, reason, entry_ts, exit_ts)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, trade.asset_type, trade.entry_price, exit_price,
                trade.quantity, pnl, pnl_pct, reason,
                trade.entry_ts, time.time(),
            ))
            self._conn.commit()

            pnl_flag = "✓" if pnl >= 0 else "✗"
            log.info(
                "%s CLOSED %s  entry=%.4f exit=%.4f  pnl=$%.2f (%.2f%%)  [%s]",
                pnl_flag, symbol, trade.entry_price, exit_price, pnl, pnl_pct, reason,
            )
            return trade

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, symbol: str) -> Optional[OpenTrade]:
        with self._lock:
            return self._memory.get(symbol)

    def all_open(self) -> list[OpenTrade]:
        with self._lock:
            return list(self._memory.values())

    def count(self) -> int:
        with self._lock:
            return len(self._memory)

    def has(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._memory

    # ── Stats ─────────────────────────────────────────────────────────────────

    def daily_pnl(self) -> float:
        cur = self._conn.execute(
            "SELECT SUM(pnl) FROM closed_trades WHERE exit_ts > ?",
            (time.time() - 86_400,),
        )
        row = cur.fetchone()
        return float(row[0] or 0)

    def daily_trade_count(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM closed_trades WHERE exit_ts > ?",
            (time.time() - 86_400,),
        )
        row = cur.fetchone()
        return int(row[0] or 0)

    def print_summary(self) -> None:
        pnl   = self.daily_pnl()
        count = self.daily_trade_count()
        open_ = self.count()
        log.info(
            "── SUMMARY  daily_pnl=$%.2f  closed=%d  open=%d ──",
            pnl, count, open_,
        )
