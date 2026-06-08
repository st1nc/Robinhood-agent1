# Robinhood Sniper Agent

A 24/7 automated trading agent for Robinhood that scans stocks (regular +
extended hours Mon–Fri) and crypto (always) for high-probability sniper entries,
then auto-exits at tight profit targets or stop-losses.

> **Default mode is paper trading.** Set `PAPER_TRADING=false` in `.env` only
> when you are ready to risk real money.

---

## Strategy

| Signal | Weight |
|---|---|
| RSI deeply oversold (< 30) | +2 pts |
| RSI recovering from oversold | +1 pt |
| Price at / below Bollinger lower band | +1 pt |
| MACD histogram turning bullish | +1 pt |
| Volume spike >= 2x average | +1 pt |
| 3 consecutive up-candles after pullback | +1 pt |

A trade is entered only when composite score >= `MIN_SIGNAL_SCORE` (default 3).

### Exit rules (stocks)
- **Profit target**: +2 % -> sell
- **Stop-loss**: -0.8 % -> sell
- **Max hold**: 30 min -> sell regardless

### Exit rules (crypto)
- **Profit target**: +2.5 %
- **Stop-loss**: -1.0 %
- **Max hold**: 60 min

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# edit .env — add Robinhood username + password (+ MFA secret if enabled)

# 3. Run in paper mode (safe default)
python main.py

# 4. Go live (only when you are confident)
PAPER_TRADING=false python main.py
```

### Running 24/7 with systemd

```ini
# /etc/systemd/system/sniper.service
[Unit]
Description=Robinhood Sniper Agent
After=network.target

[Service]
WorkingDirectory=/path/to/Robinhood-agent1
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable sniper
sudo systemctl start sniper
sudo journalctl -fu sniper
```

---

## Configuration

All tunable parameters live in `robinhood_sniper/config.py`.
Key settings:

| Parameter | Default | Description |
|---|---|---|
| `POSITION_SIZE_PCT` | 0.10 | Fraction of buying power per trade |
| `MAX_OPEN_POSITIONS` | 5 | Max simultaneous positions |
| `PROFIT_TARGET_PCT` | 0.020 | Stock profit target (+2 %) |
| `STOP_LOSS_PCT` | 0.008 | Stock stop-loss (-0.8 %) |
| `MAX_HOLD_MINUTES` | 30 | Force-exit after N minutes |
| `MIN_SIGNAL_SCORE` | 3 | Min confluence score to enter |
| `MAX_DAILY_LOSS_PCT` | 0.05 | Kill-switch at -5 % daily loss |
| `SCAN_INTERVAL_SECONDS` | 30 | Watchlist rescan cadence |
| `VOLUME_SPIKE_MULTIPLIER` | 2.0 | Volume x N avg = spike |

---

## Architecture

```
main.py                         <- orchestrator + event loop
robinhood_sniper/
  config.py                     <- all parameters
  broker/
    robinhood_client.py         <- robin_stocks wrapper (live)
  market/
    hours.py                    <- session detection (pre/regular/after/closed)
  analysis/
    signals.py                  <- RSI, BB, MACD, volume scoring
  scanner/
    opportunity.py              <- continuous watchlist scanner (thread)
  execution/
    trader.py                   <- entry/exit execution (thread)
  risk/
    manager.py                  <- trade gate + circuit-breakers
  positions/
    tracker.py                  <- SQLite-backed position store
  paper/
    simulator.py                <- paper-trade broker (same interface as live)
```

---

---

# Solana Sniper Agent

A second, independent agent (`solana_main.py`) that snipes **freshly-launched
Solana tokens** on-chain. It discovers new mints, screens them for rug/honeypot
risk, snipes qualifying entries through the [Jupiter](https://jup.ag)
aggregator, and auto-exits at profit targets, trailing stops, or stop-losses.

> **Default mode is paper trading.** Set `PAPER_TRADING=false` in `.env` only
> when you've funded a wallet and reviewed the safety filters. On-chain sniping
> is high-risk: most new tokens are scams or instantly dumped.

## How it works

```
listing feed  ─►  safety screen  ─►  priority queue  ─►  snipe  ─►  exit monitor
(DexScreener)     (rug filters)       (by score)        (Jupiter)   (TP/SL/trail)
```

### Safety screen (0–8 score)

The screen is deliberately strict: the goal is to **only enter genuine launches
and skip the overwhelming majority that are scams**. Every candidate must clear
*every* hard gate **and** reach `MIN_SAFETY_SCORE` (default 6).

| Check | Type |
|---|---|
| Pool age within `MIN_POOL_AGE_SECONDS … MAX_POOL_AGE_MINUTES` | hard gate |
| Not already +`MAX_PRICE_CHANGE_5M_PCT`% in 5 min (no chasing tops) | hard gate |
| Liquidity ≥ `MIN_LIQUIDITY_USD` | hard gate + 1 pt |
| Liquidity / FDV ≥ `MIN_LIQUIDITY_FDV_RATIO` (no thin-pool/huge-cap) | gate² + 1 pt |
| 5-minute volume ≥ `MIN_VOLUME_5M_USD` | +1 pt |
| Buy ratio ≥ `MIN_BUY_RATIO` (not being dumped) | gate² + 1 pt |
| Mint authority renounced (no infinite-mint rug) | gate¹ + 1 pt |
| Freeze authority renounced (tokens can't be frozen) | gate¹ + 1 pt |
| Top **non-pool** holder ≤ `MAX_TOP_HOLDER_PCT` | gate³ + 1 pt |
| Round-trip loss ≤ `MAX_ROUNDTRIP_LOSS_PCT` (sellable, low tax) | hard gate + 1 pt |

¹ enforced when `REQUIRE_MINT_RENOUNCED` / `REQUIRE_FREEZE_RENOUNCED` are true.
² enforced once the data is available (FDV known / ≥ 5 trades in the window).
³ enforced when the on-chain holder lookup succeeds.

The round-trip check buys then immediately sell-quotes the *real* trade size via
Jupiter — no route back is a classic honeypot signature and is rejected. The
top-holder check excludes the AMM pool vault so it flags real whale/dev wallets,
not normal pooled liquidity. The minimum pool age lets a launch settle for a few
seconds so these reads reflect reality instead of block-zero noise.

### Exit rules — fast in, fast out

- **Rug guard (highest priority)**: bail immediately if pool liquidity drops
  `LIQUIDITY_RUG_EXIT_PCT` (35 %) below entry — liquidity is being pulled
- **Profit target**: +35 % (`PROFIT_TARGET_PCT`)
- **Trailing stop**: give back 15 % off the peak (`TRAILING_STOP_PCT`)
- **Hard stop**: -18 % (`STOP_LOSS_PCT`)
- **Max hold**: 20 min (`MAX_HOLD_MINUTES`)

Positions are re-checked every `MONITOR_INTERVAL_SECONDS` (3 s).

## Quick start

```bash
pip install -r requirements.txt        # installs solana + solders for live mode
cp .env.example .env                    # set SOLANA_RPC_URL (and WALLET_PRIVATE_KEY for live)

python solana_main.py                   # paper mode (safe default)
PAPER_TRADING=false python solana_main.py   # live — funds a real wallet
```

Paper mode needs no wallet — it screens with live market data plus a read-only
RPC (for mint/freeze authority and holder checks) and simulates fills with
slippage. Because those on-chain checks are required to confirm a token isn't a
scam, the screen won't reach `MIN_SAFETY_SCORE` if the RPC is unreachable, so it
simply won't trade — `solana`/`solders` installed and a working `SOLANA_RPC_URL`
are needed even for realistic paper runs. A dedicated RPC (Helius / QuickNode /
Triton) is strongly recommended; the public node is heavily rate-limited.

## Architecture

```
solana_main.py                  <- orchestrator + event loop
solana_sniper/
  config.py                     <- all parameters (SOL-denominated)
  chain/client.py               <- Solana RPC + Jupiter swaps + DexScreener (live)
  sources/listings.py           <- new-mint discovery feed
  analysis/filters.py           <- rug/honeypot safety screen → score
  scanner/sniper.py             <- continuous scanner (thread)
  execution/trader.py           <- snipe entry + TP/SL/trailing exit monitor
  risk/manager.py               <- trade gate + daily-loss kill-switch
  positions/tracker.py          <- SQLite-backed position store (survives restarts)
  paper/simulator.py            <- paper broker (same interface as live)
```

Tunables live in `solana_sniper/config.py` and can be overridden via `.env`.

---

## Risk disclosure

Automated trading carries significant financial risk. Past performance of any
strategy does not guarantee future results. Always start in paper mode, review
logs, and size positions conservatively before enabling live trading.

On-chain memecoin sniping is **especially** high-risk: liquidity can be pulled
at any moment, "renounced" authorities do not guarantee safety, and slippage on
illiquid pools can be severe. Never snipe with funds you can't afford to lose,
and use a dedicated burner wallet — never your main holdings.
