"""Official Robinhood broker via the MCP HTTP API.

Makes JSON-RPC calls directly to https://agent.robinhood.com/mcp/trading
using a bearer token obtained via the OAuth 2.0 PKCE flow.

All tool calls mirror the MCP server's tool interface.  Requires
AGENTIC_ACCOUNT and MCP_BEARER_TOKEN in the environment.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

import requests

from robinhood_sniper import config

log = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
})

_RETRY_DELAYS = (2, 4, 8, 16)


def _call(tool: str, arguments: dict) -> Optional[dict]:
    """Send a JSON-RPC tools/call to the MCP server."""
    if not config.MCP_BEARER_TOKEN:
        log.error("MCP_BEARER_TOKEN not set — cannot call MCP server")
        return None

    headers = {"Authorization": f"Bearer {config.MCP_BEARER_TOKEN}"}
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }

    for attempt, delay in enumerate((None, *_RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            resp = _SESSION.post(
                config.MCP_SERVER_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                log.error("MCP error from %s: %s", tool, body["error"])
                return None
            result = body.get("result", {})
            # Unwrap MCP content envelope
            contents = result.get("content", [])
            for item in contents:
                if item.get("type") == "text":
                    import json
                    try:
                        return json.loads(item["text"])
                    except Exception:
                        return {"raw": item["text"]}
            return result
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                log.error("MCP auth error — token expired? Re-authenticate via Claude Code.")
                return None
            log.warning("MCP HTTP error on %s (attempt %d): %s", tool, attempt + 1, e)
        except Exception as e:
            log.warning("MCP call failed on %s (attempt %d): %s", tool, attempt + 1, e)

    log.error("MCP call %s failed after all retries", tool)
    return None


class MCPBroker:
    """Live Robinhood broker via the official MCP HTTP API."""

    def __init__(self) -> None:
        self._account = config.AGENTIC_ACCOUNT
        if not config.MCP_BEARER_TOKEN:
            log.warning(
                "MCP_BEARER_TOKEN not set. "
                "Run 'claude mcp add robinhood-trading --transport http "
                "https://agent.robinhood.com/mcp/trading' and authenticate, "
                "then set MCP_BEARER_TOKEN in .env"
            )

    # ── Account ───────────────────────────────────────────────────────────────

    def get_buying_power(self) -> float:
        data = _call("get_portfolio", {"account_number": self._account})
        if data:
            try:
                return float(data["data"]["buying_power"]["buying_power"])
            except (KeyError, TypeError, ValueError):
                pass
        return 0.0

    def get_portfolio_equity(self) -> float:
        data = _call("get_portfolio", {"account_number": self._account})
        if data:
            try:
                return float(data["data"]["total_value"])
            except (KeyError, TypeError, ValueError):
                pass
        return 0.0

    def get_daily_pnl(self) -> float:
        return 0.0  # tracked locally by PositionTracker

    def get_daily_trades(self) -> int:
        return 0

    # ── Quotes ────────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> Optional[float]:
        data = _call("get_equity_quotes", {"symbols": [symbol]})
        if not data:
            return None
        try:
            results = data["data"]["results"]
            if not results:
                return None
            q = results[0]["quote"]
            # Pick whichever timestamp is more recent
            reg_time  = q.get("venue_last_trade_time", "")
            ext_time  = q.get("venue_last_non_reg_trade_time", "")
            if ext_time > reg_time and q.get("last_non_reg_trade_price"):
                return float(q["last_non_reg_trade_price"])
            return float(q["last_trade_price"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def get_crypto_quote(self, symbol: str) -> Optional[float]:
        # MCP server crypto support is separate — fall back to None for now
        return None

    # ── Historical Bars (for TA) ──────────────────────────────────────────────

    def get_historicals(self, symbol: str, asset_type: str) -> list[dict]:
        """MCP server does not expose historicals — caller must use robin_stocks."""
        return []

    # ── Orders ────────────────────────────────────────────────────────────────

    def buy(self, symbol: str, asset_type: str, dollars: float) -> Optional[str]:
        if asset_type == "crypto":
            log.warning("Crypto buy not yet supported via MCP broker")
            return None

        price = self.get_quote(symbol)
        if not price or price <= 0:
            log.warning("Cannot buy %s: no valid quote", symbol)
            return None

        quantity = round(dollars / price, 6)
        limit_price = str(round(price * 1.002, 2))   # 0.2 % above ask

        # Determine session
        from robinhood_sniper.market.hours import extended_hours_active
        market_hours = "extended_hours" if extended_hours_active() else "regular_hours"

        result = _call("place_equity_order", {
            "account_number": self._account,
            "symbol":         symbol,
            "side":           "buy",
            "type":           "limit",
            "quantity":       str(quantity),
            "limit_price":    limit_price,
            "time_in_force":  "gfd",
            "market_hours":   market_hours,
            "ref_id":         str(uuid.uuid4()),
        })

        if result:
            order_id = result.get("data", {}).get("id", "unknown")
            log.info("BUY  %s  qty=%.4f  @$%s  id=%s", symbol, quantity, limit_price, order_id)
            return order_id
        log.error("BUY order failed for %s", symbol)
        return None

    def sell(
        self,
        symbol:     str,
        asset_type: str,
        quantity:   Optional[float] = None,
    ) -> Optional[str]:
        if asset_type == "crypto":
            log.warning("Crypto sell not yet supported via MCP broker")
            return None

        if not quantity:
            pos = self.get_position(symbol, asset_type)
            if not pos:
                return None
            quantity = pos.get("qty", 0)
        if quantity <= 0:
            return None

        price = self.get_quote(symbol)
        if not price:
            return None
        limit_price = str(round(price * 0.998, 2))   # 0.2 % below bid

        from robinhood_sniper.market.hours import extended_hours_active
        market_hours = "extended_hours" if extended_hours_active() else "regular_hours"

        result = _call("place_equity_order", {
            "account_number": self._account,
            "symbol":         symbol,
            "side":           "sell",
            "type":           "limit",
            "quantity":       str(round(quantity, 6)),
            "limit_price":    limit_price,
            "time_in_force":  "gfd",
            "market_hours":   market_hours,
            "ref_id":         str(uuid.uuid4()),
        })

        if result:
            order_id = result.get("data", {}).get("id", "unknown")
            log.info("SELL %s  qty=%.6f  @$%s  id=%s", symbol, quantity, limit_price, order_id)
            return order_id
        log.error("SELL order failed for %s", symbol)
        return None

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_position(self, symbol: str, asset_type: str) -> Optional[dict]:
        data = _call("get_equity_positions", {"account_number": self._account})
        if not data:
            return None
        try:
            for pos in data.get("data", {}).get("results", []):
                if pos.get("symbol") == symbol:
                    return {
                        "qty":      float(pos.get("quantity", 0)),
                        "avg_cost": float(pos.get("average_buy_price", 0)),
                        "type":     "stock",
                    }
        except (KeyError, TypeError, ValueError):
            pass
        return None

    def get_open_positions(self) -> dict[str, dict]:
        data = _call("get_equity_positions", {"account_number": self._account})
        result = {}
        if not data:
            return result
        try:
            for pos in data.get("data", {}).get("results", []):
                sym = pos.get("symbol")
                qty = float(pos.get("quantity", 0))
                if sym and qty > 0:
                    result[sym] = {
                        "qty":      qty,
                        "avg_cost": float(pos.get("average_buy_price", 0)),
                        "type":     "stock",
                    }
        except (KeyError, TypeError, ValueError):
            pass
        return result
