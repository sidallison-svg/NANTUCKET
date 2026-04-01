"""
Price alert management.

Alerts fire when a ticker's price crosses a user-defined threshold.
All alerts are persisted in the SQLite `alerts` table.

The dashboard background task calls check_alerts() every 60 seconds
using the current watchlist quotes dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from nantucket.db.database import get_connection

if TYPE_CHECKING:
    from nantucket.data.stocks import StockQuote


@dataclass
class Alert:
    id: int
    ticker: str
    direction: str          # "above" | "below"
    target: float
    note: str
    active: bool
    created_at: str
    triggered_at: Optional[str]


def _row_to_alert(row) -> Alert:
    return Alert(
        id=row["id"],
        ticker=row["ticker"],
        direction=row["direction"],
        target=row["target"],
        note=row["note"] or "",
        active=bool(row["active"]),
        created_at=row["created_at"],
        triggered_at=row["triggered_at"],
    )


def add_alert(
    ticker: str,
    direction: str,
    target: float,
    note: str = "",
) -> Alert:
    """Create a new price alert. direction must be 'above' or 'below'."""
    if direction not in ("above", "below"):
        raise ValueError("direction must be 'above' or 'below'")
    ticker = ticker.upper()
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO alerts (ticker, direction, target, note)
               VALUES (?, ?, ?, ?)""",
            (ticker, direction, target, note),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM alerts WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row_to_alert(row)


def remove_alert(alert_id: int) -> bool:
    """Delete an alert by ID. Returns True if a row was deleted."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        conn.commit()
        return cur.rowcount > 0


def get_alerts(
    ticker: Optional[str] = None,
    active_only: bool = False,
) -> list[Alert]:
    """Return alerts, optionally filtered by ticker or active status."""
    query = "SELECT * FROM alerts"
    params: list = []
    conditions: list[str] = []

    if ticker:
        conditions.append("ticker = ?")
        params.append(ticker.upper())
    if active_only:
        conditions.append("active = 1")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_alert(r) for r in rows]


def get_recent_triggers(limit: int = 10) -> list[Alert]:
    """Return recently triggered (fired) alerts, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE active = 0 ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_alert(r) for r in rows]


def check_alerts(quotes: "dict[str, StockQuote]") -> list[Alert]:
    """
    Check all active alerts against the provided quote dict.
    Triggered alerts are marked inactive with a triggered_at timestamp.
    Returns the list of newly triggered Alert objects.
    """
    active = get_alerts(active_only=True)
    triggered: list[Alert] = []
    now = datetime.now().isoformat()

    with get_connection() as conn:
        for alert in active:
            q = quotes.get(alert.ticker)
            if q is None or q.price <= 0:
                continue
            fired = (
                (alert.direction == "above" and q.price >= alert.target)
                or
                (alert.direction == "below" and q.price <= alert.target)
            )
            if fired:
                conn.execute(
                    "UPDATE alerts SET active = 0, triggered_at = ? WHERE id = ?",
                    (now, alert.id),
                )
                alert.active = False
                alert.triggered_at = now
                triggered.append(alert)
        if triggered:
            conn.commit()

    return triggered
