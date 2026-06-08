import os
from dotenv import load_dotenv

load_dotenv()

# ── Official MCP API (primary) ────────────────────────────────────────────────
MCP_SERVER_URL      = os.getenv("MCP_SERVER_URL", "https://agent.robinhood.com/mcp/trading")
MCP_BEARER_TOKEN    = os.getenv("MCP_BEARER_TOKEN", "")
AGENTIC_ACCOUNT     = os.getenv("AGENTIC_ACCOUNT", "690935523")  # agentic_allowed account

# ── robin_stocks fallback credentials ────────────────────────────────────────
ROBINHOOD_USERNAME  = os.getenv("ROBINHOOD_USERNAME", "")
ROBINHOOD_PASSWORD  = os.getenv("ROBINHOOD_PASSWORD", "")
ROBINHOOD_MFA_SECRET = os.getenv("ROBINHOOD_MFA_SECRET", "")

# ── Mode ──────────────────────────────────────────────────────────────────────
PAPER_TRADING  = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_BALANCE  = float(os.getenv("PAPER_BALANCE", "10000.0"))

# ── Watchlists ────────────────────────────────────────────────────────────────
CRYPTO_WATCHLIST = [
    "BTC", "ETH", "DOGE", "SOL", "ADA", "MATIC", "AVAX",
    "LINK", "UNI", "SHIB", "LTC", "BCH", "XLM", "ALGO",
]

STOCK_WATCHLIST = [
    # High-beta / high-volatility equities & ETFs
    "SPY", "QQQ", "TSLA", "NVDA", "AMD", "META", "AMZN",
    "AAPL", "MSFT", "GOOGL", "NFLX", "GME", "AMC", "PLTR",
    "RIVN", "LCID", "SOFI", "HOOD", "COIN", "MARA", "RIOT",
    "SQQQ", "TQQQ", "SPXU", "UPRO", "ARKK", "SOXL",
]

# ── Market-Hours ──────────────────────────────────────────────────────────────
TIMEZONE          = "America/New_York"
PREMARKET_START   = "04:00"
MARKET_OPEN       = "09:30"
MARKET_CLOSE      = "16:00"
AFTERMARKET_END   = "20:00"
TRADE_EXTENDED_HOURS = True

# ── Scanning ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 30      # Full watchlist rescan cadence
CANDLE_INTERVAL       = "5minute"
HISTORICALS_SPAN      = "day"

# ── Technical-Analysis Parameters ────────────────────────────────────────────
RSI_PERIOD    = 14
RSI_OVERSOLD  = 30
RSI_OVERBOUGHT = 70

BB_PERIOD = 20
BB_STD    = 2.0

MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9

VOLUME_SPIKE_MULTIPLIER = 2.0   # vol > Nx rolling avg  → spike
VOLUME_AVG_PERIODS      = 20

# Minimum composite signal score (0–6) to qualify for a trade
MIN_SIGNAL_SCORE = 3

# ── Trade Sizing ──────────────────────────────────────────────────────────────
POSITION_SIZE_PCT  = 0.10        # 10 % of available buying power per trade
MIN_TRADE_DOLLARS  = 10.0
MAX_TRADE_DOLLARS  = 5_000.0

# ── Exit Parameters — Stocks ─────────────────────────────────────────────────
PROFIT_TARGET_PCT = 0.020        # +2 %
STOP_LOSS_PCT     = 0.008        # -0.8 %
MAX_HOLD_MINUTES  = 30

# ── Exit Parameters — Crypto ─────────────────────────────────────────────────
CRYPTO_PROFIT_TARGET_PCT = 0.025  # +2.5 %
CRYPTO_STOP_LOSS_PCT     = 0.010  # -1.0 %
CRYPTO_MAX_HOLD_MINUTES  = 60

# ── Risk Controls ─────────────────────────────────────────────────────────────
MAX_OPEN_POSITIONS  = 5
MAX_DAILY_LOSS_PCT  = 0.05       # Halt if daily P&L < -5 %
MAX_DAILY_TRADES    = 50         # Guard against pattern-day-trader rule
POSITION_COOLDOWN_S = 300        # Seconds before re-entering same symbol

# ── Persistence ───────────────────────────────────────────────────────────────
DB_PATH = "positions.db"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = "sniper.log"
