"""Risk gate — every trade must pass before execution."""
from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import TYPE_CHECKING

from robinhood_sniper import config

if TYPE_CHECKING:
    from robinhood_sniper.positions.tracker import PositionTracker

log = logging.getLogger(__name__)


class Decision(Enum):
    ALLOW = auto()
    BLOCK = auto()


class RiskManager:

    def __init__(self, tracker: "PositionTracker") -> None:
        self._tracker       = tracker
        self._halted        = False
        self._cooldowns: dict[str, float] = {}   # symbol → next-allowed timestamp
        self._start_equity  = 0.0

    def set_start_equity(self, equity: float) -> None:
        self._start_equity = equity

    def halt(self) -> None:
        self._halted = True
        log.warning("Risk manager HALTED trading")

    def is_halted(self) -> bool:
        return self._halted

    def record_cooldown(self, symbol: str) -> None:
        self._cooldowns[symbol] = time.time() + config.POSITION_COOLDOWN_S

    def check(self, symbol: str, dollars: float, equity: float) -> tuple[Decision, str]:
        """Return (ALLOW, "") or (BLOCK, reason)."""

        if self._halted:
            return Decision.BLOCK, "Trading halted"

        # 1. Daily loss limit
        daily_pnl = self._tracker.daily_pnl()
        if self._start_equity > 0:
            daily_loss_pct = -daily_pnl / self._start_equity
            if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
                self.halt()
                return Decision.BLOCK, (
                    f"Daily loss limit reached: {daily_loss_pct:.1%} >= {config.MAX_DAILY_LOSS_PCT:.1%}"
                )

        # 2. Max open positions
        if self._tracker.count() >= config.MAX_OPEN_POSITIONS:
            return Decision.BLOCK, (
                f"Max open positions ({config.MAX_OPEN_POSITIONS}) reached"
            )

        # 3. Already holding this symbol
        if self._tracker.has(symbol):
            return Decision.BLOCK, f"Already in a position for {symbol}"

        # 4. Cooldown after recent trade in same symbol
        cooldown_until = self._cooldowns.get(symbol, 0)
        if time.time() < cooldown_until:
            remaining = int(cooldown_until - time.time())
            return Decision.BLOCK, f"{symbol} on cooldown ({remaining}s remaining)"

        # 5. Daily trade count
        if self._tracker.daily_trade_count() >= config.MAX_DAILY_TRADES:
            return Decision.BLOCK, f"Daily trade limit ({config.MAX_DAILY_TRADES}) reached"

        # 6. Position size sanity
        if dollars < config.MIN_TRADE_DOLLARS:
            return Decision.BLOCK, f"Trade size ${dollars:.2f} below minimum ${config.MIN_TRADE_DOLLARS}"

        if equity > 0 and (dollars / equity) > 0.50:
            return Decision.BLOCK, f"Single trade ${dollars:.2f} > 50 % of equity (circuit breaker)"

        return Decision.ALLOW, ""
