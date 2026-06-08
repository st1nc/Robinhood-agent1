"""Risk gate — every snipe must pass before execution."""
from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import TYPE_CHECKING

from solana_sniper import config

if TYPE_CHECKING:
    from solana_sniper.positions.tracker import PositionTracker

log = logging.getLogger(__name__)


class Decision(Enum):
    ALLOW = auto()
    BLOCK = auto()


class RiskManager:

    def __init__(self, tracker: "PositionTracker") -> None:
        self._tracker = tracker
        self._halted = False
        self._cooldowns: dict[str, float] = {}    # mint → next-allowed timestamp

    def halt(self) -> None:
        self._halted = True
        log.warning("Risk manager HALTED trading")

    def is_halted(self) -> bool:
        return self._halted

    def record_cooldown(self, mint: str) -> None:
        self._cooldowns[mint] = time.time() + config.POSITION_COOLDOWN_S

    def check(self, mint: str, sol_amount: float, balance: float) -> tuple[Decision, str]:
        """Return (ALLOW, "") or (BLOCK, reason)."""

        if self._halted:
            return Decision.BLOCK, "Trading halted"

        # 1. Daily loss kill-switch
        daily_pnl = self._tracker.daily_pnl()
        if daily_pnl <= -config.MAX_DAILY_LOSS_SOL:
            self.halt()
            return Decision.BLOCK, (
                f"Daily loss limit hit: {daily_pnl:.3f} SOL <= -{config.MAX_DAILY_LOSS_SOL} SOL"
            )

        # 2. Max open positions
        if self._tracker.count() >= config.MAX_OPEN_POSITIONS:
            return Decision.BLOCK, f"Max open positions ({config.MAX_OPEN_POSITIONS}) reached"

        # 3. Already holding this mint
        if self._tracker.has(mint):
            return Decision.BLOCK, f"Already holding {mint[:6]}"

        # 4. Per-mint cooldown after a recent exit
        cooldown_until = self._cooldowns.get(mint, 0)
        if time.time() < cooldown_until:
            remaining = int(cooldown_until - time.time())
            return Decision.BLOCK, f"{mint[:6]} on cooldown ({remaining}s)"

        # 5. Daily trade count
        if self._tracker.daily_trade_count() >= config.MAX_DAILY_TRADES:
            return Decision.BLOCK, f"Daily trade limit ({config.MAX_DAILY_TRADES}) reached"

        # 6. Trade-size sanity
        if sol_amount < config.MIN_TRADE_SOL:
            return Decision.BLOCK, f"Trade size {sol_amount:.3f} SOL below minimum"

        # 7. Don't spend more SOL than we hold (keep a buffer for fees)
        if balance > 0 and sol_amount > balance - 0.01:
            return Decision.BLOCK, (
                f"Insufficient balance: need {sol_amount:.3f} SOL, have {balance:.3f}"
            )

        return Decision.ALLOW, ""
