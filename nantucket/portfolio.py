"""
Paper trading portfolio tracker.

Paper trading = simulated trading with fake money, real prices.
Why paper trade?
- Practice making buy/sell decisions without risking real money
- Test trading strategies and see how they actually perform
- Build intuition about position sizing and P&L

How P&L is calculated:
- Average Cost Basis: When you buy 10 shares at $100 and 5 more at $110,
  your avg cost = (10*100 + 5*110) / 15 = $103.33/share
- Unrealized P&L: (Current Price - Avg Cost) × Shares Held
- Realized P&L: When you sell, the profit/loss is "realized" (locked in)
- Total Return %: Unrealized P&L / Total Cost Basis × 100

This module handles:
1. Recording buy/sell trades in the trades table
2. Computing current positions from trade history
3. Fetching live prices to calculate current P&L
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nantucket.db.database import get_connection
from nantucket.data.stocks import get_quotes_batch, StockQuote


# ──────────────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """
    A current open position in the portfolio.

    cost_basis: How much you paid per share on average
    current_price: Where it's trading right now
    unrealized_pnl: Paper profit or loss (positive = profit)
    """
    ticker: str
    asset_type: str
    quantity: float
    avg_cost: float
    current_price: float = 0.0
    current_value: float = 0.0
    cost_basis_total: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    allocation_pct: float = 0.0     # % of total portfolio value
    error: Optional[str] = None


@dataclass
class Trade:
    """A single recorded trade."""
    id: int
    ticker: str
    asset_type: str
    action: str       # "buy" or "sell"
    quantity: float
    price: float
    timestamp: str


@dataclass
class PortfolioSummary:
    """Overall portfolio stats."""
    positions: list[Position]
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    cash: float = 0.0   # Placeholder for future cash tracking


# ──────────────────────────────────────────────────────────────────────────────
# Trade recording
# ──────────────────────────────────────────────────────────────────────────────

def record_buy(
    ticker: str,
    quantity: float,
    price: float,
    asset_type: str = "stock",
) -> int:
    """
    Record a paper buy trade.

    Returns the trade ID.
    Raises ValueError if quantity or price is invalid.
    """
    ticker = ticker.upper().strip()
    if quantity <= 0:
        raise ValueError(f"Quantity must be positive, got {quantity}")
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")

    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO trades (ticker, asset_type, action, quantity, price)
               VALUES (?, ?, 'buy', ?, ?)""",
            (ticker, asset_type.lower(), quantity, price),
        )
        conn.commit()
        return cursor.lastrowid


def record_sell(
    ticker: str,
    quantity: float,
    price: float,
    asset_type: str = "stock",
) -> int:
    """
    Record a paper sell trade.

    Raises ValueError if you're trying to sell more than you own.
    Returns the trade ID.
    """
    ticker = ticker.upper().strip()
    if quantity <= 0:
        raise ValueError(f"Quantity must be positive, got {quantity}")
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")

    # Check we actually have enough shares
    current_qty = _get_current_quantity(ticker)
    if quantity > current_qty:
        raise ValueError(
            f"Cannot sell {quantity} shares of {ticker} — you only hold {current_qty:.4f}"
        )

    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO trades (ticker, asset_type, action, quantity, price)
               VALUES (?, ?, 'sell', ?, ?)""",
            (ticker, asset_type.lower(), quantity, price),
        )
        conn.commit()
        return cursor.lastrowid


def _get_current_quantity(ticker: str) -> float:
    """Calculate current holdings of a ticker from trade history."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN action='buy'  THEN quantity ELSE 0 END), 0)
               - COALESCE(SUM(CASE WHEN action='sell' THEN quantity ELSE 0 END), 0)
               AS net_qty
               FROM trades WHERE ticker = ?""",
            (ticker.upper(),),
        ).fetchone()
        return row["net_qty"] if row else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Position calculation
# ──────────────────────────────────────────────────────────────────────────────

def get_positions_raw() -> list[dict]:
    """
    Calculate current positions from trade history using SQL aggregation.

    For each ticker, we compute:
    - net_quantity: total bought minus total sold
    - avg_cost: weighted average cost of remaining shares

    Average cost uses FIFO accounting (first in, first out) would be more
    accurate but complex. We use weighted average cost which is simpler:
        avg_cost = total_cost_of_buys / total_shares_bought

    This isn't perfect (it doesn't account for shares already sold) but it's
    a reasonable approximation for paper trading.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT
                 ticker,
                 asset_type,
                 SUM(CASE WHEN action='buy'  THEN quantity ELSE 0 END) as total_bought,
                 SUM(CASE WHEN action='sell' THEN quantity ELSE 0 END) as total_sold,
                 SUM(CASE WHEN action='buy'  THEN quantity ELSE 0 END)
               - SUM(CASE WHEN action='sell' THEN quantity ELSE 0 END) as net_quantity,
                 SUM(CASE WHEN action='buy'  THEN quantity * price ELSE 0 END)
               / NULLIF(SUM(CASE WHEN action='buy' THEN quantity ELSE 0 END), 0) as avg_cost
               FROM trades
               GROUP BY ticker, asset_type
               HAVING net_quantity > 0.0001
               ORDER BY ticker"""
        ).fetchall()
        return [dict(row) for row in rows]


def get_trade_history(ticker: Optional[str] = None, limit: int = 50) -> list[Trade]:
    """
    Get trade history, optionally filtered to a single ticker.
    Returns most recent trades first.
    """
    with get_connection() as conn:
        if ticker:
            rows = conn.execute(
                """SELECT id, ticker, asset_type, action, quantity, price, timestamp
                   FROM trades WHERE ticker = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, ticker, asset_type, action, quantity, price, timestamp
                   FROM trades ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [Trade(**dict(row)) for row in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio summary with live prices
# ──────────────────────────────────────────────────────────────────────────────

def get_portfolio_summary(show_progress: bool = False) -> PortfolioSummary:
    """
    Build a full portfolio summary with current P&L.

    Flow:
    1. Get positions from trade history (raw quantity + avg cost)
    2. Fetch current prices for all held tickers
    3. Calculate value, P&L, and allocation % for each position
    4. Sum up totals
    """
    raw_positions = get_positions_raw()
    if not raw_positions:
        return PortfolioSummary(
            positions=[], total_value=0, total_cost=0,
            total_pnl=0, total_pnl_pct=0
        )

    # Fetch current prices for all held tickers
    tickers = [p["ticker"] for p in raw_positions]
    quotes = get_quotes_batch(tickers, max_workers=10)

    # Build Position objects
    positions: list[Position] = []
    for raw in raw_positions:
        ticker = raw["ticker"]
        qty = raw["net_quantity"]
        avg_cost = raw["avg_cost"] or 0.0
        cost_total = qty * avg_cost

        quote = quotes.get(ticker)
        if quote and not quote.error and quote.price > 0:
            current_price = quote.price
            current_value = qty * current_price
            pnl = current_value - cost_total
            pnl_pct = (pnl / cost_total * 100) if cost_total > 0 else 0.0
            error = None
        else:
            current_price = avg_cost   # Fall back to cost if no price
            current_value = cost_total
            pnl = 0.0
            pnl_pct = 0.0
            error = quote.error if quote else "No data"

        positions.append(Position(
            ticker=ticker,
            asset_type=raw["asset_type"],
            quantity=qty,
            avg_cost=avg_cost,
            current_price=current_price,
            current_value=current_value,
            cost_basis_total=cost_total,
            unrealized_pnl=pnl,
            unrealized_pnl_pct=pnl_pct,
            error=error,
        ))

    # Calculate totals
    total_value = sum(p.current_value for p in positions)
    total_cost = sum(p.cost_basis_total for p in positions)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

    # Set allocation percentages
    for p in positions:
        p.allocation_pct = (p.current_value / total_value * 100) if total_value > 0 else 0.0

    # Sort by current value (largest position first)
    positions.sort(key=lambda p: p.current_value, reverse=True)

    return PortfolioSummary(
        positions=positions,
        total_value=total_value,
        total_cost=total_cost,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
    )
