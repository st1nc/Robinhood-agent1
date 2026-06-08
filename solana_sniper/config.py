"""Solana sniper configuration.

All tunable parameters live here. Secrets are pulled from the environment
(.env — see .env.example). Defaults are conservative and paper-mode safe.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── Chain / Wallet ────────────────────────────────────────────────────────────
SOLANA_RPC_URL  = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
# Base58-encoded secret key of the trading wallet (Phantom export format).
# REQUIRED only for live trading. Never commit this.
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")

# Wrapped-SOL mint — the quote asset every snipe is priced against.
SOL_MINT  = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9

# ── Mode ──────────────────────────────────────────────────────────────────────
PAPER_TRADING    = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_BALANCE_SOL = float(os.getenv("PAPER_BALANCE_SOL", "10.0"))

# ── Discovery Sources ─────────────────────────────────────────────────────────
# DexScreener public API (no key required) is used to discover freshly-created
# Solana pairs and to price open positions.
DEXSCREENER_BASE = "https://api.dexscreener.com"
CHAIN_ID         = "solana"

# Optionally restrict the DEXs we will snipe on (empty = allow all).
ALLOWED_DEXES: list[str] = []          # e.g. ["raydium", "pumpswap", "meteora"]

# ── Swap / Routing (Jupiter aggregator) ───────────────────────────────────────
JUPITER_BASE  = os.getenv("JUPITER_BASE", "https://quote-api.jup.ag/v6")
SLIPPAGE_BPS  = int(os.getenv("SLIPPAGE_BPS", "300"))   # 300 bps = 3 %
PRIORITY_FEE_LAMPORTS = int(os.getenv("PRIORITY_FEE_LAMPORTS", "200000"))

# ── Scanning ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))

# Only snipe pairs created within this window (the "fresh launch" zone).
MAX_POOL_AGE_MINUTES  = float(os.getenv("MAX_POOL_AGE_MINUTES", "30"))

# ── Safety Filters (rug / honeypot protection) ────────────────────────────────
# A candidate must clear these gates AND reach MIN_SAFETY_SCORE to qualify.
MIN_LIQUIDITY_USD     = float(os.getenv("MIN_LIQUIDITY_USD", "5000"))
MIN_VOLUME_5M_USD     = float(os.getenv("MIN_VOLUME_5M_USD", "1000"))
MAX_TOP_HOLDER_PCT    = float(os.getenv("MAX_TOP_HOLDER_PCT", "25"))   # largest non-pool holder
REQUIRE_MINT_RENOUNCED   = os.getenv("REQUIRE_MINT_RENOUNCED",   "true").lower() == "true"
REQUIRE_FREEZE_RENOUNCED = os.getenv("REQUIRE_FREEZE_RENOUNCED", "true").lower() == "true"
# Reject if a single round-trip (buy then immediate sell quote) loses more than
# this fraction to tax/slippage — a crude honeypot / high-tax detector.
MAX_ROUNDTRIP_LOSS_PCT   = float(os.getenv("MAX_ROUNDTRIP_LOSS_PCT", "0.15"))

# Minimum composite safety score (0–6) required to fire.
MIN_SAFETY_SCORE = int(os.getenv("MIN_SAFETY_SCORE", "4"))

# ── Trade Sizing (denominated in SOL) ─────────────────────────────────────────
TRADE_SIZE_SOL = float(os.getenv("TRADE_SIZE_SOL", "0.25"))
MIN_TRADE_SOL  = float(os.getenv("MIN_TRADE_SOL",  "0.05"))
MAX_TRADE_SOL  = float(os.getenv("MAX_TRADE_SOL",  "1.0"))

# ── Exit Parameters ───────────────────────────────────────────────────────────
PROFIT_TARGET_PCT  = float(os.getenv("PROFIT_TARGET_PCT",  "0.50"))   # +50 %
STOP_LOSS_PCT      = float(os.getenv("STOP_LOSS_PCT",      "0.25"))   # -25 %
TRAILING_STOP_PCT  = float(os.getenv("TRAILING_STOP_PCT",  "0.20"))   # give back 20 % off peak
MAX_HOLD_MINUTES   = float(os.getenv("MAX_HOLD_MINUTES",   "60"))

# ── Risk Controls ─────────────────────────────────────────────────────────────
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
MAX_DAILY_LOSS_SOL = float(os.getenv("MAX_DAILY_LOSS_SOL", "2.0"))    # kill-switch
MAX_DAILY_TRADES   = int(os.getenv("MAX_DAILY_TRADES", "50"))
POSITION_COOLDOWN_S = int(os.getenv("POSITION_COOLDOWN_S", "600"))    # per-mint re-entry guard

# ── Persistence ───────────────────────────────────────────────────────────────
DB_PATH = os.getenv("SOLANA_DB_PATH", "solana_positions.db")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = "solana_sniper.log"
