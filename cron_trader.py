#!/usr/bin/env python3
"""Stateless cron sniper for GitHub Actions.

Runs on a schedule (see .github/workflows/sniper.yml). Each invocation is a
fresh, short-lived process: it reads live state from the Robinhood account,
makes ONE decision, acts, and exits. No local state is required — the
position itself is the source of truth, and the intraday high used for the
trailing stop is pulled fresh from yfinance each run.

Behaviour
---------
* If the market is closed (regular hours), it exits immediately.
* If a position in SNIPER_SYMBOL is held, it evaluates exit rules:
    - take profit at +TARGET_PCT
    - hard stop at -HARD_STOP        (disabled when HARD_STOP == 0)
    - trailing stop: arms at +TRAIL_ACTIVE, trails TRAIL_STEP below the
      intraday high
    - EOD force-exit at 15:50 ET
  and sells if any fire.
* If no position is held and SNIPER_ENTRY_ENABLED is true, it buys once per
  day inside the entry window (skips if a buy already exists today).

Everything is configured through environment variables so the same script
serves any symbol / risk profile without code changes.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import uuid

import pytz
import requests

# Token is resolved once at startup via token_manager (auto-refreshes if needed).
sys.path.insert(0, os.path.dirname(__file__))
try:
    from token_manager import get_token
except ImportError:
    def get_token():
        return os.environ.get("MCP_BEARER_TOKEN", "")

# ── Config (all from env) ──────────────────────────────────────────────
TOKEN        = get_token()
ACCOUNT      = os.environ.get("AGENTIC_ACCOUNT", "")
SYMBOL       = os.environ.get("SNIPER_SYMBOL", "").upper().strip()
CAPITAL      = float(os.environ.get("SNIPER_CAPITAL", "5.00"))
TARGET_PCT   = float(os.environ.get("SNIPER_TARGET_PCT", "0.25"))
HARD_STOP    = float(os.environ.get("SNIPER_HARD_STOP", "0"))      # 0 = off
TRAIL_ACTIVE = float(os.environ.get("SNIPER_TRAIL_ACTIVE", "0.08"))
TRAIL_STEP   = float(os.environ.get("SNIPER_TRAIL_STEP", "0.03"))
ENTRY_ON     = os.environ.get("SNIPER_ENTRY_ENABLED", "false").lower() == "true"
ENTRY_START  = os.environ.get("SNIPER_ENTRY_START", "09:30")       # ET HH:MM
ENTRY_END    = os.environ.get("SNIPER_ENTRY_END", "10:30")         # ET HH:MM
EOD_EXIT     = (15, 50)

MCP_URL = "https://agent.robinhood.com/mcp/trading"
ET      = pytz.timezone("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("cron_sniper")


# ── MCP transport ──────────────────────────────────────────────────────
def mcp(tool: str, args: dict):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    for attempt, delay in enumerate((0, 2, 4, 8)):
        if delay:
            import time
            time.sleep(delay)
        try:
            r = requests.post(MCP_URL, json=payload, headers=headers,
                              timeout=20, stream=True)
            if r.status_code in (401, 403):
                log.error("AUTH FAILED (%s) — MCP_BEARER_TOKEN expired or invalid", r.status_code)
                return None
            r.raise_for_status()
            body = ""
            if "event-stream" in r.headers.get("content-type", ""):
                for raw in r.iter_lines():
                    line = raw.decode() if isinstance(raw, bytes) else raw
                    if line.startswith("data:"):
                        body = line[5:].strip()
                        break
            else:
                body = r.text
            if not body:
                continue
            data = json.loads(body)
            if "error" in data:
                log.error("MCP error from %s: %s", tool, data["error"])
                return None
            for item in data.get("result", {}).get("content", []):
                if item.get("type") == "text":
                    try:
                        return json.loads(item["text"])
                    except Exception:
                        return {"raw": item["text"]}
            return data.get("result", {})
        except Exception as e:
            log.warning("MCP %s attempt %d failed: %s", tool, attempt + 1, e)
    log.error("MCP %s failed after retries", tool)
    return None


# ── Helpers ────────────────────────────────────────────────────────────
def market_open(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


def get_price(symbol: str):
    d = mcp("get_equity_quotes", {"symbols": [symbol]})
    if not d:
        return None
    try:
        q = d["data"]["results"][0]["quote"]
        reg = q.get("venue_last_trade_time") or ""
        ext = q.get("venue_last_non_reg_trade_time") or ""
        non_reg = q.get("last_non_reg_trade_price")
        if ext and reg and ext > reg and non_reg:
            return float(non_reg)
        return float(q["last_trade_price"])
    except Exception as e:
        log.warning("price parse error: %s", e)
        return None


def get_intraday_high(symbol: str, fallback: float) -> float:
    """Day's high from yfinance so the trailing stop survives across runs."""
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="1d", interval="5m")
        if not hist.empty:
            return float(hist["High"].max())
    except Exception as e:
        log.warning("intraday high lookup failed (%s) — using current price", e)
    return fallback


def get_position(symbol: str):
    d = mcp("get_equity_positions", {"account_number": ACCOUNT})
    if not d:
        return None
    try:
        for pos in d.get("data", {}).get("positions", []):
            if pos.get("symbol") == symbol:
                qty = float(pos.get("shares_available_for_sells", 0) or 0)
                if qty > 0:
                    return {
                        "qty": qty,
                        "avg": float(pos.get("average_buy_price", 0) or 0),
                    }
    except Exception as e:
        log.warning("position parse error: %s", e)
    return None


def bought_today() -> bool:
    """Guard against double-entry across 5-minute runs."""
    d = mcp("get_equity_orders", {"account_number": ACCOUNT})
    if not d:
        return False
    today = datetime.datetime.now(ET).date().isoformat()
    try:
        for o in d.get("data", {}).get("orders", []):
            if (o.get("symbol") == SYMBOL
                    and o.get("side") == "buy"
                    and str(o.get("created_at", "")).startswith(today)
                    and o.get("state") not in ("cancelled", "rejected", "failed")):
                return True
    except Exception:
        pass
    return False


def buying_power() -> float:
    d = mcp("get_portfolio", {"account_number": ACCOUNT})
    if not d:
        return 0.0
    try:
        return float(d["data"]["buying_power"]["buying_power"])
    except Exception:
        return 0.0


def place_buy(dollars: float):
    log.info("BUY %s $%.2f market", SYMBOL, dollars)
    r = mcp("place_equity_order", {
        "account_number": ACCOUNT, "symbol": SYMBOL, "side": "buy",
        "type": "market", "dollar_amount": str(round(dollars, 2)),
        "time_in_force": "gfd", "market_hours": "regular_hours",
        "ref_id": str(uuid.uuid4()),
    })
    if r:
        o = r.get("data", {}).get("order", {})
        log.info("BUY placed id=%s state=%s", o.get("id", "?"), o.get("state", "?"))
        return True
    log.error("BUY failed")
    return False


def place_sell(qty: float, reason: str):
    log.info("SELL %s qty=%.6f (%s)", SYMBOL, qty, reason)
    r = mcp("place_equity_order", {
        "account_number": ACCOUNT, "symbol": SYMBOL, "side": "sell",
        "type": "market", "quantity": str(round(qty, 6)),
        "time_in_force": "gfd", "market_hours": "regular_hours",
        "ref_id": str(uuid.uuid4()),
    })
    if r:
        o = r.get("data", {}).get("order", {})
        log.info("SELL placed id=%s state=%s", o.get("id", "?"), o.get("state", "?"))
        return True
    log.error("SELL failed")
    return False


def in_entry_window(now: datetime.datetime) -> bool:
    sh, sm = map(int, ENTRY_START.split(":"))
    eh, em = map(int, ENTRY_END.split(":"))
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now <= end


# ── Main ───────────────────────────────────────────────────────────────
def main() -> int:
    if not TOKEN or not ACCOUNT or not SYMBOL:
        log.error("Missing required env: MCP_BEARER_TOKEN / AGENTIC_ACCOUNT / SNIPER_SYMBOL")
        return 1

    now = datetime.datetime.now(ET)
    log.info("=== cron sniper @ %s ET | %s ===", now.strftime("%Y-%m-%d %H:%M:%S"), SYMBOL)
    log.info("cfg: capital=$%.2f target=+%.0f%% hard_stop=%s trail(arm=+%.0f%%,step=%.0f%%) entry=%s",
             CAPITAL, TARGET_PCT * 100,
             f"-{HARD_STOP*100:.0f}%" if HARD_STOP > 0 else "OFF",
             TRAIL_ACTIVE * 100, TRAIL_STEP * 100, ENTRY_ON)

    if not market_open(now):
        log.info("Market closed — nothing to do.")
        return 0

    pos = get_position(SYMBOL)

    # ── Manage open position ──
    if pos:
        entry, qty = pos["avg"], pos["qty"]
        price = get_price(SYMBOL)
        if not price:
            log.error("No price — skipping this run")
            return 0

        pnl = (price - entry) / entry * 100
        high = max(get_intraday_high(SYMBOL, price), price)
        target = entry * (1 + TARGET_PCT)
        log.info("HOLD %s | entry=$%.4f cur=$%.4f P&L=%+.1f%% high=$%.4f target=$%.2f",
                 SYMBOL, entry, price, pnl, high, target)

        # EOD force-exit
        if (now.hour, now.minute) >= EOD_EXIT:
            place_sell(qty, "EOD")
            return 0

        # Take profit
        if price >= target:
            place_sell(qty, f"target +{pnl:.1f}%")
            return 0

        # Trailing stop (armed once high cleared +TRAIL_ACTIVE)
        if high >= entry * (1 + TRAIL_ACTIVE):
            trail = high * (1 - TRAIL_STEP)
            log.info("trailing armed: stop=$%.4f", trail)
            if price <= trail:
                place_sell(qty, f"trail {pnl:+.1f}%")
                return 0

        # Hard stop (only if explicitly enabled)
        if HARD_STOP > 0 and price <= entry * (1 - HARD_STOP):
            place_sell(qty, f"hard stop {pnl:+.1f}%")
            return 0

        log.info("No exit triggered — holding.")
        return 0

    # ── No position: maybe enter ──
    if not ENTRY_ON:
        log.info("No position and entry disabled — idle.")
        return 0

    if not in_entry_window(now):
        log.info("No position, outside entry window %s-%s ET — idle.", ENTRY_START, ENTRY_END)
        return 0

    if bought_today():
        log.info("Already bought %s today — not re-entering.", SYMBOL)
        return 0

    bp = buying_power()
    capital = min(CAPITAL, bp)
    log.info("Entry window | buying_power=$%.2f capital=$%.2f", bp, capital)
    if capital < 1.0:
        log.error("Insufficient buying power ($%.2f) — need >= $1", capital)
        return 0

    place_buy(capital)
    return 0


if __name__ == "__main__":
    sys.exit(main())
