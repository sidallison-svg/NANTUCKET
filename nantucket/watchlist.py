"""
Watchlist management — add, remove, and display tickers you're tracking.

The watchlist is stored in SQLite so it persists between sessions.
Each entry stores the ticker, asset type, and when it was added.

When you run `nantucket watch show`, this module:
1. Loads all tickers from the DB
2. Fetches current quotes (using the appropriate data source per asset type)
3. Returns them for the CLI/dashboard to display
"""

from __future__ import annotations

from typing import Optional

from nantucket.db.database import get_connection
from nantucket.data.stocks import StockQuote, get_quotes_batch
from nantucket.data.crypto import get_crypto_quote, CryptoQuote


# ──────────────────────────────────────────────────────────────────────────────
# CRUD operations
# ──────────────────────────────────────────────────────────────────────────────

def add_ticker(ticker: str, asset_type: str = "stock") -> bool:
    """
    Add a ticker to the watchlist.

    Returns True if added successfully, False if it was already there.
    asset_type should be one of: stock, etf, crypto, future
    """
    ticker = ticker.upper().strip()
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist (ticker, asset_type) VALUES (?, ?)",
                (ticker, asset_type.lower()),
            )
            conn.commit()
            return True
        except Exception:
            # UNIQUE constraint violation — already in watchlist
            return False


def remove_ticker(ticker: str) -> bool:
    """
    Remove a ticker from the watchlist.
    Returns True if removed, False if it wasn't in the list.
    """
    ticker = ticker.upper().strip()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE ticker = ?", (ticker,)
        )
        conn.commit()
        return cursor.rowcount > 0


def get_watchlist() -> list[dict]:
    """
    Return all watchlist entries as a list of dicts with keys:
    ticker, asset_type, date_added
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker, asset_type, date_added FROM watchlist ORDER BY date_added ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def is_in_watchlist(ticker: str) -> bool:
    """Check if a ticker is already in the watchlist."""
    ticker = ticker.upper().strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row is not None


def clear_watchlist() -> int:
    """Remove all tickers from the watchlist. Returns count removed."""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM watchlist")
        conn.commit()
        return cursor.rowcount


# ──────────────────────────────────────────────────────────────────────────────
# Live quotes for watchlist
# ──────────────────────────────────────────────────────────────────────────────

def get_watchlist_quotes(show_progress: bool = False) -> list[StockQuote]:
    """
    Fetch current market data for all tickers in the watchlist.

    Uses different data sources based on asset_type:
    - stock/etf/future → yfinance (get_quotes_batch for parallel speed)
    - crypto → CoinGecko (individual calls for now)

    Returns a list of StockQuote objects (crypto quotes are converted to
    StockQuote for uniform display).
    """
    entries = get_watchlist()
    if not entries:
        return []

    # Split by data source
    stock_tickers = [
        e["ticker"] for e in entries
        if e["asset_type"] in ("stock", "etf", "future")
    ]
    crypto_tickers = [
        e["ticker"] for e in entries
        if e["asset_type"] == "crypto"
    ]

    results: list[StockQuote] = []

    # Fetch stocks/ETFs/futures in parallel
    if stock_tickers:
        if show_progress:
            from rich.progress import Progress, SpinnerColumn, TextColumn
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]Fetching watchlist data..."),
                transient=True,
            ) as progress:
                progress.add_task("Loading", total=None)
                batch = get_quotes_batch(stock_tickers, max_workers=10)
        else:
            batch = get_quotes_batch(stock_tickers, max_workers=10)

        # Preserve the order from the watchlist
        for entry in entries:
            if entry["asset_type"] in ("stock", "etf", "future"):
                q = batch.get(entry["ticker"])
                if q:
                    # Restore the correct asset_type from our DB record
                    q.asset_type = entry["asset_type"]
                    results.append(q)

    # Fetch crypto sequentially (CoinGecko free tier is rate-limited)
    for ticker in crypto_tickers:
        cq = get_crypto_quote(ticker)
        # Convert CryptoQuote to StockQuote for uniform display
        sq = _crypto_to_stock_quote(cq)
        results.append(sq)

    return results


def _crypto_to_stock_quote(cq: CryptoQuote) -> StockQuote:
    """Convert a CryptoQuote to a StockQuote for unified display."""
    from nantucket.data.stocks import StockQuote
    return StockQuote(
        ticker=cq.ticker,
        name=cq.name,
        price=cq.price,
        change_pct=cq.change_pct,
        volume=int(cq.volume_24h),
        market_cap=cq.market_cap,
        asset_type="crypto",
        change_1w=cq.change_7d_pct,
        error=cq.error,
    )
