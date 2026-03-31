"""
Database table schemas for NANTUCKET.

Each CREATE TABLE statement uses "IF NOT EXISTS" so it's safe to run
on every startup — it won't wipe your data if the table already exists.

Why SQLite?
- No server to run, no installation, just a single file on disk
- Portable: your data travels with the app
- Python has built-in support via the `sqlite3` module
- Fast enough for personal-scale data (thousands of rows)
"""

# ──────────────────────────────────────────────────────────────────────────────
# Watchlist: stocks/ETFs/crypto you want to keep an eye on
# ──────────────────────────────────────────────────────────────────────────────
CREATE_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL UNIQUE,
    asset_type TEXT NOT NULL DEFAULT 'stock',   -- stock | etf | crypto | future
    date_added TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# ──────────────────────────────────────────────────────────────────────────────
# Trades: every paper buy/sell you execute
# Keeping full trade history (not just current positions) lets us reconstruct
# P&L at any point in time and learn from past decisions.
# ──────────────────────────────────────────────────────────────────────────────
CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT    NOT NULL,
    asset_type TEXT    NOT NULL DEFAULT 'stock',
    action     TEXT    NOT NULL,   -- buy | sell
    quantity   REAL    NOT NULL,
    price      REAL    NOT NULL,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now'))
)
"""

# ──────────────────────────────────────────────────────────────────────────────
# Saved screens: reusable filter combinations you've named
# filters_json stores the filter dict as a JSON string, e.g.:
#   '{"pe_max": 15, "pb_max": 1.5, "eps_min": 0}'
# ──────────────────────────────────────────────────────────────────────────────
CREATE_SAVED_SCREENS = """
CREATE TABLE IF NOT EXISTS saved_screens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    filters_json TEXT NOT NULL,
    date_created TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# ──────────────────────────────────────────────────────────────────────────────
# Alerts: price alerts that fire when a ticker crosses a threshold
# direction is 'above' or 'below'; active 1=waiting, 0=triggered
# ──────────────────────────────────────────────────────────────────────────────
CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    direction    TEXT    NOT NULL,   -- above | below
    target       REAL    NOT NULL,
    note         TEXT    NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    triggered_at TEXT
)
"""

# ──────────────────────────────────────────────────────────────────────────────
# Econ cache: generic key/value cache for non-ticker data (FRED, news, etc.)
# Kept separate from quote_cache so ticker-based keys don't collide
# ──────────────────────────────────────────────────────────────────────────────
CREATE_ECON_CACHE = """
CREATE TABLE IF NOT EXISTS econ_cache (
    cache_key  TEXT NOT NULL PRIMARY KEY,
    data_json  TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# Run all of these on startup
ALL_TABLES = [CREATE_WATCHLIST, CREATE_TRADES, CREATE_SAVED_SCREENS, CREATE_ALERTS, CREATE_ECON_CACHE]
