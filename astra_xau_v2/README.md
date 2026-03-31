# Astra XAU v2

Multi-symbol XAU scalping bot for MetaTrader 5. Targets XAUUSD, XAUEUR, and XAUGBP on a Funding Pips $50,000 prop account.

## Targets

- **$300** minimum profit per symbol per day
- **$500** minimum total daily profit (floor alert at 18:00)
- **$3,000** maximum daily profit cap (stops all trading)
- **100 pips** coverage per symbol per day
- Lot sizes auto-calculated from account equity and live pip values

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your MT5 credentials, MongoDB URI, notification webhooks
```

### 3. Required: MT5 Terminal

The bot connects to a running MetaTrader 5 terminal. Ensure MT5 is open and logged in on the same machine.

## Running Live

```bash
cd astra_xau_v2
python scheduler/daily_init.py
```

This starts:
- Trading engines for all 3 symbols (5-second tick loop)
- Terminal dashboard (Rich) with live PnL/status
- Web dashboard at `http://localhost:8050` with candlestick charts
- APScheduler: daily reset at 00:01, floor check at 18:00 (MT5 server time)

## Running Backtest

```bash
cd astra_xau_v2
python backtest/engine.py --start 2025-01-01 --end 2025-12-31
```

Options:
- `--start` / `--end` — date range (YYYY-MM-DD)
- `--timeframe` — M1, M5, M15, M30, H1, H4, D1 (default: M15)
- `--equity` — starting equity (default: 50000)
- `--symbols XAUUSD XAUEUR` — specific symbols
- `--no-report` — skip HTML report generation

The backtest will:
1. Load historical data from CSV or MT5 (generates synthetic data if neither available)
2. Run the scalper + hawk filter on each candle (no lookahead)
3. Simulate trades with spread, slippage, and commission
4. Generate an HTML report with 5 embedded charts and open it in your browser

Report saved to `data/backtest_results/report_{timestamp}.html`.

## Viewing the Live Dashboard

Open `http://localhost:8050` in your browser while the bot is running. Shows:
- Per-symbol status cards (ACTIVE/FROZEN), PnL, pips
- Global stats: total PnL, distance to cap/floor
- Live M15 candlestick charts with trade markers

## MT5 Chart Annotations

When running live, the bot draws objects directly on your MT5 chart:
- **Entry lines** — green (BUY) or red (SELL) horizontal lines at entry price
- **SL lines** — orange dashed horizontal lines
- **TP lines** — blue dashed horizontal lines
- **Entry arrows** — up/down arrows at the entry candle
- **Exit markers** — green (WIN) or red (LOSS) arrows at exit candle
- **Summary label** — corner label showing PnL, pips, and status

Objects are prefixed with `astra_` and cleared daily at reset.

## Funding Pips Compliance

| Rule | Implementation |
|------|---------------|
| Max 5% daily drawdown | `PROP_FIRM_MAX_DAILY_DD = 0.05` in settings |
| Max 10% total drawdown | `PROP_FIRM_MAX_TOTAL_DD = 0.10` in settings |
| Risk per trade | 1% equity split across active symbols |
| News avoidance | 30-min block before/after high-impact events |
| Daily profit cap | $3,000 hard stop — closes all positions |
| Per-symbol target | $300 — freezes symbol, closes its positions |

## Interpreting Backtest Charts

### Equity Curve
Shows account equity over time. Per-symbol contribution lines show which symbols are driving returns. Red shaded regions indicate drawdown from peak.

### Trade Scatter (per symbol)
Candlestick chart with entry markers (triangles), exit markers (circles), and SL/TP lines connecting entry to exit.

### Daily PnL
Bar chart colored by performance: green ($500+), amber ($300-499), red (<$300). Horizontal lines at $500 floor and $3,000 cap.

### Drawdown Chart
Shows drawdown percentage from equity peak. Yellow line at 3% (warning zone), red line at 5% (Funding Pips daily limit).

### Win/Loss Distribution
Histogram of pips per trade for wins (green) and losses (red), with mean lines and profit factor annotation.

## Running Tests

```bash
cd astra_xau_v2
python -m pytest tests/ -v
```

## Architecture

```
config/     — settings, symbol configs
core/       — MT5 client, market data, news filter
capital/    — lot size allocator, profit guard
strategy/   — scalper (entry), hawk filter (confirmation)
executor/   — trading engine, runner, order manager
state/      — atomic JSON state per symbol
backtest/   — engine, simulator, data loader, report, charts
monitor/    — terminal dashboard, web dashboard, MT5 chart bridge
logger/     — MongoDB trade log, Discord/Telegram notifications
scheduler/  — APScheduler daily init
```
