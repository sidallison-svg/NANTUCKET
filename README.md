# ⚓ NANTUCKET

Personal investment screener, portfolio tracker, and market learning tool.

## Quick Start

```bash
# Install
pip install -e .

# Add some tickers to your watchlist
nantucket watch add AAPL MSFT NVDA GOOGL

# Show watchlist with live prices
nantucket watch show

# Run the value investing screen
nantucket screen --preset value

# Run the momentum screen on the full S&P 500
nantucket screen --preset momentum --universe sp500

# Custom screen: tech stocks, P/E under 25, above 50-day MA
nantucket screen --sector Technology --pe-below 25 --above-50ma

# Get a quick quote
nantucket quote AAPL MSFT TSLA

# Log a paper trade
nantucket trade buy AAPL 10 --price 185.50
nantucket trade sell AAPL 5

# View portfolio P&L
nantucket portfolio

# AI analysis (requires ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
nantucket explain AAPL
nantucket bull-bear TSLA

# Start the web dashboard
nantucket dashboard
```

## Commands

| Command | Description |
|---------|-------------|
| `nantucket screen` | Screen stocks with filters or presets |
| `nantucket watch add TICKERS` | Add to watchlist |
| `nantucket watch show` | Show watchlist with live prices |
| `nantucket watch remove TICKERS` | Remove from watchlist |
| `nantucket trade buy TICKER QTY` | Log a paper buy |
| `nantucket trade sell TICKER QTY` | Log a paper sell |
| `nantucket trade history` | View trade log |
| `nantucket portfolio` | View positions + P&L |
| `nantucket quote TICKERS` | Quick price check |
| `nantucket explain TICKER` | AI: why is this moving? |
| `nantucket bull-bear TICKER` | AI: bull vs bear case |
| `nantucket dashboard` | Open web dashboard |

## Screener Presets

| Preset | Description |
|--------|-------------|
| `value` | Low P/E, low P/B, profitable |
| `momentum` | Above 50MA, high relative volume, strong week |
| `dividend` | Yield > 3%, solid earnings |
| `growth` | Revenue growth > 20% YoY |
| `oversold` | Near 52-week low |
| `large_cap` | Market cap > $100B |
| `volume_spike` | 2x+ average daily volume |

## Custom Screens

```bash
# All filters
nantucket screen --pe-below 15 --pb-below 1.5 --sector Healthcare
nantucket screen --volume-ratio-min 2.0 --above-50ma
nantucket screen --near-52w-low 10 --market-cap-above 5B
nantucket screen --change-1w-above 5 --volume-above 1M

# Save a custom screen
nantucket screen --pe-below 20 --sector Technology --save my-tech

# List all presets
nantucket screen --list-presets
```

## Setup

### 1. Install dependencies

```bash
pip install -e .
```

### 2. Set up AI analysis (optional)

Get a free API key at [console.anthropic.com](https://console.anthropic.com) and set:

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Add this to your `~/.bashrc` or `~/.zshrc` to make it permanent.

### 3. Data is stored locally

All your watchlist and trade data lives in `~/.nantucket/nantucket.db` — a single SQLite file.
- **Backup**: just copy this file
- **Reset**: delete this file (loses all data)

## Data Sources

- **Stocks & ETFs**: [Yahoo Finance](https://finance.yahoo.com) via `yfinance` (free, no key needed)
- **Crypto**: [CoinGecko](https://www.coingecko.com) free API (no key needed)
- **Futures**: Yahoo Finance via `yfinance` (GC=F, CL=F, etc.)
- **AI Analysis**: [Anthropic Claude](https://anthropic.com) API (key required)

## Project Structure

```
nantucket/
├── nantucket/
│   ├── cli.py          # All CLI commands (Typer)
│   ├── screener.py     # Screening engine + filter logic
│   ├── watchlist.py    # Watchlist CRUD
│   ├── portfolio.py    # Paper trading + P&L
│   ├── data/
│   │   ├── stocks.py   # yfinance wrapper
│   │   ├── crypto.py   # CoinGecko wrapper
│   │   └── futures.py  # Futures/commodities
│   ├── ai/
│   │   └── analyst.py  # Claude API integration
│   ├── db/
│   │   ├── database.py # SQLite connection
│   │   └── models.py   # Table schemas
│   └── dashboard/
│       ├── server.py   # FastAPI app
│       ├── templates/  # Jinja2 HTML templates
│       └── static/     # CSS, JS, Chart.js
├── presets/
│   └── default_screens.json
└── pyproject.toml
```
