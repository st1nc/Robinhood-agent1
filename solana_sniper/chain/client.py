"""Live Solana broker client.

Combines three public surfaces behind one tidy interface:

  * DexScreener  — pair discovery + USD/SOL pricing (no API key)
  * Solana RPC   — mint authority / decimals / largest-holder lookups
  * Jupiter v6   — swap quotes and on-chain swap execution

Heavy on-chain dependencies (``solana`` / ``solders``) are imported lazily so
the package — and paper trading — work without a wallet or signing libraries
installed.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import requests

from solana_sniper import config

log = logging.getLogger(__name__)

_RETRY_DELAYS = (2, 4, 8, 16)
_TIMEOUT = 10


def _retry(fn, *args, **kwargs):
    """Call fn; retry up to 4× with exponential back-off on exceptions."""
    for delay in (None, *_RETRY_DELAYS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:                       # noqa: BLE001
            if delay is None:
                log.debug("call failed (%s), retrying…", exc)
                continue
            log.warning("call failed (%s), sleeping %ds before retry…", exc, delay)
            time.sleep(delay)
    log.error("call failed after all retries: %s", getattr(fn, "__name__", fn))
    return None


class SolanaClient:
    """Live on-chain trading client."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._keypair = None        # lazily created solders Keypair
        self._rpc = None            # lazily created solana RPC client
        self._decimals_cache: dict[str, int] = {}

    # ── Wallet / connection ───────────────────────────────────────────────────

    def login(self) -> bool:
        """Load the wallet keypair and verify RPC connectivity."""
        if not config.WALLET_PRIVATE_KEY:
            log.error("WALLET_PRIVATE_KEY not set in .env")
            return False
        try:
            from solders.keypair import Keypair          # type: ignore
            from solana.rpc.api import Client             # type: ignore

            self._keypair = Keypair.from_base58_string(config.WALLET_PRIVATE_KEY)
            self._rpc = Client(config.SOLANA_RPC_URL)
            pubkey = str(self._keypair.pubkey())
            bal = self.get_portfolio_equity()
            log.info("Wallet loaded: %s  (%.4f SOL)", pubkey, bal)
            return True
        except ImportError:
            log.error("solana / solders not installed — run: pip install solana solders")
            return False
        except Exception as exc:                          # noqa: BLE001
            log.error("Wallet login failed: %s", exc)
            return False

    def logout(self) -> None:
        self._keypair = None
        self._rpc = None

    @property
    def pubkey(self) -> Optional[str]:
        return str(self._keypair.pubkey()) if self._keypair else None

    # ── Account ───────────────────────────────────────────────────────────────

    def get_buying_power(self) -> float:
        """Spendable SOL balance (whole SOL)."""
        return self.get_portfolio_equity()

    def get_portfolio_equity(self) -> float:
        if not self._rpc or not self._keypair:
            return 0.0
        resp = _retry(self._rpc.get_balance, self._keypair.pubkey())
        try:
            return (resp.value or 0) / 1e9
        except Exception:                                 # noqa: BLE001
            return 0.0

    # ── Pricing / market data (DexScreener) ───────────────────────────────────

    def get_token_market(self, mint: str) -> Optional[dict]:
        """Return the most-liquid pair's market snapshot for ``mint``.

        Keys: price_usd, price_native (SOL/token), liquidity_usd,
        volume_5m_usd, pair_created_ms, dex_id, pair_address, symbol, name.
        """
        url = f"{config.DEXSCREENER_BASE}/latest/dex/tokens/{mint}"
        data = _retry(self._get_json, url)
        pairs = (data or {}).get("pairs") or []
        sol_pairs = [
            p for p in pairs
            if p.get("chainId") == config.CHAIN_ID
            and p.get("quoteToken", {}).get("address") == config.SOL_MINT
        ]
        candidates = sol_pairs or [p for p in pairs if p.get("chainId") == config.CHAIN_ID]
        if not candidates:
            return None

        best = max(candidates, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        liq = (best.get("liquidity") or {}).get("usd") or 0.0
        vol5 = (best.get("volume") or {}).get("m5") or 0.0
        base = best.get("baseToken") or {}
        return {
            "price_usd":      float(best.get("priceUsd") or 0) or None,
            "price_native":   float(best.get("priceNative") or 0) or None,
            "liquidity_usd":  float(liq),
            "volume_5m_usd":  float(vol5),
            "pair_created_ms": int(best.get("pairCreatedAt") or 0),
            "dex_id":         best.get("dexId", "?"),
            "pair_address":   best.get("pairAddress", ""),
            "symbol":         base.get("symbol", "?"),
            "name":           base.get("name", "?"),
        }

    def get_price_sol(self, mint: str) -> Optional[float]:
        """Current price of one token in SOL."""
        market = self.get_token_market(mint)
        if market and market.get("price_native"):
            return market["price_native"]
        return None

    # ── On-chain token safety (RPC) ───────────────────────────────────────────

    def get_mint_authorities(self, mint: str) -> dict:
        """Return {mint_renounced, freeze_renounced, decimals, supply}.

        ``*_renounced`` is True when the corresponding authority is null.
        Returns an empty dict if the lookup fails (treated as "unknown").
        """
        if not self._rpc:
            return {}
        try:
            from solders.pubkey import Pubkey            # type: ignore
            resp = _retry(
                self._rpc.get_account_info_json_parsed,
                Pubkey.from_string(mint),
            )
            info = resp.value.data.parsed["info"]        # type: ignore[union-attr]
            decimals = int(info.get("decimals", 0))
            self._decimals_cache[mint] = decimals
            return {
                "mint_renounced":   info.get("mintAuthority") in (None, ""),
                "freeze_renounced": info.get("freezeAuthority") in (None, ""),
                "decimals":         decimals,
                "supply":           float(info.get("supply", 0)),
            }
        except Exception as exc:                          # noqa: BLE001
            log.debug("mint authority lookup failed for %s: %s", mint, exc)
            return {}

    def get_top_holder_pct(self, mint: str) -> Optional[float]:
        """Largest token-account share of supply, as a percent (0–100)."""
        if not self._rpc:
            return None
        try:
            from solders.pubkey import Pubkey            # type: ignore
            largest = _retry(self._rpc.get_token_largest_accounts,
                             Pubkey.from_string(mint))
            supply = _retry(self._rpc.get_token_supply, Pubkey.from_string(mint))
            total = float(supply.value.amount)            # type: ignore[union-attr]
            top = max(float(a.amount) for a in largest.value)  # type: ignore[union-attr]
            if total <= 0:
                return None
            return top / total * 100.0
        except Exception as exc:                          # noqa: BLE001
            log.debug("top-holder lookup failed for %s: %s", mint, exc)
            return None

    def get_decimals(self, mint: str) -> int:
        if mint in self._decimals_cache:
            return self._decimals_cache[mint]
        auth = self.get_mint_authorities(mint)
        return int(auth.get("decimals", 9))

    # ── Jupiter quotes / swaps ────────────────────────────────────────────────

    def quote(self, input_mint: str, output_mint: str, amount: int) -> Optional[dict]:
        """Raw Jupiter quote. ``amount`` is in the input mint's base units."""
        params = {
            "inputMint":  input_mint,
            "outputMint": output_mint,
            "amount":     amount,
            "slippageBps": config.SLIPPAGE_BPS,
        }
        return _retry(self._get_json, f"{config.JUPITER_BASE}/quote", params=params)

    def roundtrip_loss_pct(self, mint: str, sol_amount: float) -> Optional[float]:
        """Buy then immediately sell (quote-only) — fraction of SOL lost.

        A high loss flags steep transfer tax or a honeypot. Returns None if a
        route is missing in either direction (also a red flag: unsellable).
        """
        lamports = int(sol_amount * 1e9)
        buy = self.quote(config.SOL_MINT, mint, lamports)
        if not buy or not buy.get("outAmount"):
            return None
        tokens_out = int(buy["outAmount"])
        sell = self.quote(mint, config.SOL_MINT, tokens_out)
        if not sell or not sell.get("outAmount"):
            return None
        sol_back = int(sell["outAmount"]) / 1e9
        return max(0.0, 1.0 - sol_back / sol_amount)

    # ── Orders ────────────────────────────────────────────────────────────────

    def buy(self, mint: str, sol_amount: float) -> Optional[str]:
        """Swap ``sol_amount`` SOL → ``mint``. Returns the tx signature."""
        lamports = int(sol_amount * 1e9)
        quote = self.quote(config.SOL_MINT, mint, lamports)
        if not quote:
            log.warning("No route to buy %s", mint)
            return None
        return self._execute_swap(quote)

    def sell(self, mint: str, quantity: float) -> Optional[str]:
        """Swap ``quantity`` tokens → SOL. Returns the tx signature."""
        decimals = self.get_decimals(mint)
        amount = int(quantity * (10 ** decimals))
        if amount <= 0:
            return None
        quote = self.quote(mint, config.SOL_MINT, amount)
        if not quote:
            log.warning("No route to sell %s", mint)
            return None
        return self._execute_swap(quote)

    def _execute_swap(self, quote: dict) -> Optional[str]:
        """Build, sign, and send a Jupiter swap transaction."""
        if not self._keypair or not self._rpc:
            log.error("Cannot swap: wallet not loaded")
            return None
        try:
            from solders.transaction import VersionedTransaction   # type: ignore
            from solders.message import to_bytes_versioned         # type: ignore
            from solana.rpc.types import TxOpts                     # type: ignore

            body = {
                "quoteResponse": quote,
                "userPublicKey": str(self._keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": config.PRIORITY_FEE_LAMPORTS,
            }
            resp = _retry(self._post_json, f"{config.JUPITER_BASE}/swap", json=body)
            if not resp or "swapTransaction" not in resp:
                log.error("Jupiter swap build failed: %s", resp)
                return None

            raw = base64.b64decode(resp["swapTransaction"])
            unsigned = VersionedTransaction.from_bytes(raw)
            signature = self._keypair.sign_message(
                to_bytes_versioned(unsigned.message)
            )
            signed = VersionedTransaction.populate(unsigned.message, [signature])

            send = _retry(
                self._rpc.send_raw_transaction,
                bytes(signed),
                opts=TxOpts(skip_preflight=True, max_retries=3),
            )
            sig = str(send.value)
            log.info("Swap submitted: %s", sig)
            return sig
        except ImportError:
            log.error("solana / solders not installed — cannot execute live swap")
            return None
        except Exception as exc:                          # noqa: BLE001
            log.error("Swap execution failed: %s", exc)
            return None

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get_json(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        r = self._session.get(url, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post_json(self, url: str, json: dict) -> Optional[dict]:
        r = self._session.post(url, json=json, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
