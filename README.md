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

## Risk disclosure

Automated trading carries significant financial risk. Past performance of any
strategy does not guarantee future results. Always start in paper mode, review
logs, and size positions conservatively before enabling live trading.
