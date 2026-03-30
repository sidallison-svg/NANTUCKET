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

# Run all of these on startup
ALL_TABLES = [CREATE_WATCHLIST, CREATE_TRADES, CREATE_SAVED_SCREENS]
