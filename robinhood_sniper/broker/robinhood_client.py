"""Thin wrapper around robin_stocks.

Provides a unified interface for stocks and crypto.
Authentication is handled once at startup; the session is persisted to disk
by robin_stocks so restarts don't require re-login.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from robinhood_sniper import config

rh = None
_RH_AVAILABLE = False

log = logging.getLogger(__name__)

_RETRY_DELAYS = (2, 4, 8, 16)


def _retry(fn, *args, **kwargs):
    """Call fn; retry up to 4× with exponential back-off on exceptions."""
    for delay in (None, *_RETRY_DELAYS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if delay is None:
                log.warning("API call failed (%s), retrying…", exc)
                continue
            log.warning("API call failed (%s), sleeping %ds before retry…", exc, delay)
            time.sleep(delay)
    log.error("API call failed after all retries: %s %s", fn.__name__, args)
    return None


class RobinhoodClient:
    """Live Robinhood broker client (robin_stocks based)."""

    def login(self) -> bool:
        if not _RH_AVAILABLE:
            log.error("robin_stocks not available in this environment")
            return False
        if not config.ROBINHOOD_USERNAME or not config.ROBINHOOD_PASSWORD:
            log.error("ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD not set in .env")
            return False

        mfa_code: Optional[str] = None
        if config.ROBINHOOD_MFA_SECRET:
            try:
                import pyotp
                mfa_code = pyotp.TOTP(config.ROBINHOOD_MFA_SECRET).now()
                log.info("MFA code generated via TOTP")
            except ImportError:
                log.warning("pyotp not installed; skipping TOTP MFA")

        try:
            rh.login(
                username=config.ROBINHOOD_USERNAME,
                password=config.ROBINHOOD_PASSWORD,
                mfa_code=mfa_code,
                store_session=True,
                pickle_name="rh_session",
            )
            log.info("Logged in to Robinhood as %s", config.ROBINHOOD_USERNAME)
            return True
        except Exception as exc:
            log.error("Login failed: %s", exc)
            return False

    def logout(self) -> None:
        try:
            rh.logout()
            log.info("Logged out from Robinhood")
        except Exception as exc:
            log.warning("Logout error: %s", exc)

    # ── Quotes ────────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> Optional[float]:
        data = _retry(rh.get_latest_price, symbol, includeExtendedHours=True)
        if data and len(data) > 0:
            try:
                return float(data[0])
            except (TypeError, ValueError):
                pass
        return None

    def get_crypto_quote(self, symbol: str) -> Optional[float]:
        data = _retry(rh.get_crypto_quote, symbol)
        if data:
            try:
                return float(data.get("mark_price") or data.get("ask_price", 0))
            except (TypeError, ValueError):
                pass
        return None

    # ── Historical Bars ───────────────────────────────────────────────────────

    def get_historicals(self, symbol: str, asset_type: str) -> list[dict]:
        if asset_type == "crypto":
            result = _retry(
                rh.get_crypto_historicals,
                symbol,
                interval=config.CANDLE_INTERVAL,
                span=config.HISTORICALS_SPAN,
            )
        else:
            result = _retry(
                rh.get_stock_historicals,
                symbol,
                interval=config.CANDLE_INTERVAL,
                span=config.HISTORICALS_SPAN,
                bounds="extended" if config.TRADE_EXTENDED_HOURS else "regular",
            )
        return result or []

    # ── Account ───────────────────────────────────────────────────────────────

    def get_buying_power(self) -> float:
        profile = _retry(rh.load_account_profile)
        if profile:
            try:
                return float(profile.get("buying_power", 0))
            except (TypeError, ValueError):
                pass
        return 0.0

    def get_portfolio_equity(self) -> float:
        portfolio = _retry(rh.load_portfolio_profile)
        if portfolio:
            try:
                return float(portfolio.get("equity", 0))
            except (TypeError, ValueError):
                pass
        return 0.0

    def get_daily_pnl(self) -> float:
        portfolio = _retry(rh.load_portfolio_profile)
        if portfolio:
            try:
                return float(portfolio.get("equity_previous_close", 0)) - \
                       float(portfolio.get("equity", 0))
            except (TypeError, ValueError):
                pass
        return 0.0

    def get_daily_trades(self) -> int:
        orders = _retry(rh.get_all_open_orders) or []
        return len(orders)

    # ── Orders — Stocks ───────────────────────────────────────────────────────

    def buy(self, symbol: str, asset_type: str, dollars: float) -> Optional[str]:
        price = (
            self.get_crypto_quote(symbol)
            if asset_type == "crypto"
            else self.get_quote(symbol)
        )
        if not price or price <= 0:
            log.warning("Cannot buy %s: no valid quote", symbol)
            return None

        quantity = dollars / price

        if asset_type == "crypto":
            result = _retry(
                rh.order_buy_crypto_by_price,
                symbol,
                dollars,
            )
        else:
            # Use limit order so extended hours works
            limit_price = round(price * 1.002, 2)  # 0.2 % above ask
            result = _retry(
                rh.order_buy_limit,
                symbol,
                quantity=round(quantity, 6),
                limitPrice=limit_price,
                extendedHours=True,
            )

        if result:
            order_id = result.get("id", "unknown")
            log.info("BUY  %s  $%.2f  qty=%.4f  @$%.4f  id=%s",
                     symbol, dollars, quantity, price, order_id)
            return order_id
        log.error("BUY order failed for %s", symbol)
        return None

    def sell(
        self,
        symbol:     str,
        asset_type: str,
        quantity:   Optional[float] = None,
    ) -> Optional[str]:
        if not quantity:
            pos = self.get_position(symbol, asset_type)
            if not pos:
                return None
            quantity = pos.get("qty", 0)

        if quantity <= 0:
            return None

        if asset_type == "crypto":
            result = _retry(
                rh.order_sell_crypto_by_quantity,
                symbol,
                quantity,
            )
        else:
            price = self.get_quote(symbol)
            if not price:
                return None
            limit_price = round(price * 0.998, 2)   # 0.2 % below bid
            result = _retry(
                rh.order_sell_limit,
                symbol,
                quantity=round(quantity, 6),
                limitPrice=limit_price,
                extendedHours=True,
            )

        if result:
            order_id = result.get("id", "unknown")
            log.info("SELL %s  qty=%.6f  id=%s", symbol, quantity, order_id)
            return order_id
        log.error("SELL order failed for %s", symbol)
        return None

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_position(self, symbol: str, asset_type: str) -> Optional[dict]:
        if asset_type == "crypto":
            positions = _retry(rh.get_crypto_positions) or []
            for pos in positions:
                if pos.get("currency", {}).get("code") == symbol:
                    return {
                        "qty":      float(pos.get("quantity", 0)),
                        "avg_cost": float(pos.get("cost_bases", [{}])[0].get("direct_cost_basis", 0) or 0),
                    }
        else:
            positions = _retry(rh.get_open_stock_positions) or []
            for pos in positions:
                instrument_data = _retry(rh.get_instrument_by_url, pos.get("instrument"))
                if instrument_data and instrument_data.get("symbol") == symbol:
                    qty = float(pos.get("quantity", 0))
                    avg = float(pos.get("average_buy_price", 0))
                    return {"qty": qty, "avg_cost": avg}
        return None

    def get_open_positions(self) -> dict[str, dict]:
        result = {}
        stock_positions = _retry(rh.get_open_stock_positions) or []
        for pos in stock_positions:
            qty = float(pos.get("quantity", 0))
            if qty <= 0:
                continue
            instrument_data = _retry(rh.get_instrument_by_url, pos.get("instrument"))
            if instrument_data:
                sym = instrument_data.get("symbol", "?")
                result[sym] = {
                    "qty":      qty,
                    "avg_cost": float(pos.get("average_buy_price", 0)),
                    "type":     "stock",
                }

        crypto_positions = _retry(rh.get_crypto_positions) or []
        for pos in crypto_positions:
            qty = float(pos.get("quantity", 0))
            if qty <= 1e-8:
                continue
            sym = pos.get("currency", {}).get("code", "?")
            result[sym] = {
                "qty":      qty,
                "avg_cost": float(
                    (pos.get("cost_bases") or [{}])[0].get("direct_cost_basis", 0) or 0
                ),
                "type": "crypto",
            }
        return result
