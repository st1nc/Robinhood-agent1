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

# Snipe pairs inside this freshness window. The lower floor lets a launch settle
# for a few seconds (initial bundle snipers clear, real liquidity and holders
# appear) so the safety screen reads accurate data instead of block-zero noise.
MIN_POOL_AGE_SECONDS  = float(os.getenv("MIN_POOL_AGE_SECONDS", "30"))
MAX_POOL_AGE_MINUTES  = float(os.getenv("MAX_POOL_AGE_MINUTES", "15"))

# ── Safety Filters (rug / honeypot protection) ────────────────────────────────
# A candidate must clear EVERY hard gate AND reach MIN_SAFETY_SCORE to qualify.
# Defaults are deliberately strict — the goal is to only touch tokens that look
# like genuine launches, and skip the overwhelming majority that are scams.
MIN_LIQUIDITY_USD     = float(os.getenv("MIN_LIQUIDITY_USD", "10000"))
MIN_VOLUME_5M_USD     = float(os.getenv("MIN_VOLUME_5M_USD", "2000"))

# Largest *non-pool* holder cap. The AMM pool vault is excluded; what's left is
# the biggest dev/whale wallet that could dump on you.
MAX_TOP_HOLDER_PCT    = float(os.getenv("MAX_TOP_HOLDER_PCT", "10"))

# Renounced authorities are required by default: an active mint authority can
# print unlimited supply, an active freeze authority can lock your tokens.
REQUIRE_MINT_RENOUNCED   = os.getenv("REQUIRE_MINT_RENOUNCED",   "true").lower() == "true"
REQUIRE_FREEZE_RENOUNCED = os.getenv("REQUIRE_FREEZE_RENOUNCED", "true").lower() == "true"

# Honeypot / high-tax guard: buy then immediately sell-quote TRADE_SIZE_SOL via
# Jupiter; reject if too much value is lost — or if there's no sell route at all.
MAX_ROUNDTRIP_LOSS_PCT   = float(os.getenv("MAX_ROUNDTRIP_LOSS_PCT", "0.12"))

# Healthy two-sided flow: fraction of the last 5 minutes' trades that are buys
# must be at least this. A sell-dominated pool is already being dumped.
MIN_BUY_RATIO            = float(os.getenv("MIN_BUY_RATIO", "0.45"))

# Liquidity must be a meaningful fraction of fully-diluted value, otherwise a
# tiny pool is propping up a huge nominal market cap (classic exit-scam setup).
MIN_LIQUIDITY_FDV_RATIO  = float(os.getenv("MIN_LIQUIDITY_FDV_RATIO", "0.03"))

# Don't chase a launch that has already gone vertical in the last 5 minutes —
# we want a clean early entry, not the top of someone else's pump.
MAX_PRICE_CHANGE_5M_PCT  = float(os.getenv("MAX_PRICE_CHANGE_5M_PCT", "120"))

# Minimum composite safety score (0–8) required to fire.
MIN_SAFETY_SCORE = int(os.getenv("MIN_SAFETY_SCORE", "6"))

# ── Trade Sizing (denominated in SOL) ─────────────────────────────────────────
TRADE_SIZE_SOL = float(os.getenv("TRADE_SIZE_SOL", "0.25"))
MIN_TRADE_SOL  = float(os.getenv("MIN_TRADE_SOL",  "0.05"))
MAX_TRADE_SOL  = float(os.getenv("MAX_TRADE_SOL",  "1.0"))

# ── Exit Parameters ───────────────────────────────────────────────────────────
# Fast in / fast out — lock gains early and cut losers quickly rather than
# riding a fresh launch's round-trip back to zero.
PROFIT_TARGET_PCT  = float(os.getenv("PROFIT_TARGET_PCT",  "0.35"))   # +35 %
STOP_LOSS_PCT      = float(os.getenv("STOP_LOSS_PCT",      "0.18"))   # -18 %
TRAILING_STOP_PCT  = float(os.getenv("TRAILING_STOP_PCT",  "0.15"))   # give back 15 % off peak
MAX_HOLD_MINUTES   = float(os.getenv("MAX_HOLD_MINUTES",   "20"))
MONITOR_INTERVAL_SECONDS = float(os.getenv("MONITOR_INTERVAL_SECONDS", "3"))

# Emergency rug exit: dump immediately if pool liquidity drops this fraction
# below the level we entered at — liquidity is being pulled.
LIQUIDITY_RUG_EXIT_PCT = float(os.getenv("LIQUIDITY_RUG_EXIT_PCT", "0.35"))

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
