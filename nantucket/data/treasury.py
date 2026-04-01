"""
US Treasury yield curve data via yfinance.

Tickers:
  ^IRX  = 13-week T-Bill  (quoted as annualised discount × 100 → divide by 100)
  ^FVX  = 5-year note     (quoted as yield × 10 → divide by 10)
  ^TNX  = 10-year note    (quoted as yield × 10 → divide by 10)
  ^TYX  = 30-year bond    (quoted as yield × 10 → divide by 10)

yfinance doesn't have a clean 2-year ticker so we use ^IRX for 3-month and
estimate 2-year from the 5-year slope where unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import yfinance as yf

from nantucket.data._session import get_session
from nantucket.data.stocks import _fetch_chart

# Cached in quote_cache using this synthetic key
_CACHE_TICKER = "__YIELD_CURVE__"
_CACHE_TYPE = "yield_curve"
_CACHE_TTL = 30  # minutes

# (yf_ticker, label, display_name, divisor)
YIELD_TICKERS: list[tuple[str, str, str, float]] = [
    ("^IRX", "3mo",  "3 Month",  100.0),
    ("^FVX", "5yr",  "5 Year",    10.0),
    ("^TNX", "10yr", "10 Year",   10.0),
    ("^TYX", "30yr", "30 Year",   10.0),
]


@dataclass
class YieldPoint:
    maturity: str       # "3mo", "5yr", etc.
    yield_pct: float    # e.g. 4.25  (percent)
    change_bps: float   # daily change in basis points
    label: str          # "3 Month"


@dataclass
class YieldCurve:
    points: list[YieldPoint] = field(default_factory=list)
    is_inverted: bool = False   # True when 10yr yield < 3mo yield
    spread_10y3m: float = 0.0   # 10yr minus 3mo in bps
    fetched_at: str = ""
    error: Optional[str] = None


def get_yield_curve() -> YieldCurve:
    """Fetch current Treasury yield curve with caching."""
    # Lazy import to avoid circular dependency at module load
    from nantucket.db.database import get_econ_cached, set_econ_cache

    cached = get_econ_cached(_CACHE_TICKER + _CACHE_TYPE, _CACHE_TTL)
    if cached:
        points = [YieldPoint(**p) for p in cached["points"]]
        return YieldCurve(
            points=points,
            is_inverted=cached["is_inverted"],
            spread_10y3m=cached["spread_10y3m"],
            fetched_at=cached["fetched_at"],
        )

    try:
        points: list[YieldPoint] = []
        yield_by_maturity: dict[str, float] = {}

        for yf_ticker, maturity, label, divisor in YIELD_TICKERS:
            try:
                result = _fetch_chart(yf_ticker, range_="5d", interval="1d")
                if not result:
                    continue
                closes_raw = (result.get("indicators", {})
                                    .get("quote", [{}])[0]
                                    .get("close") or [])
                closes = [c for c in closes_raw if c is not None]
                if len(closes) < 2:
                    continue
                current = closes[-1] / divisor
                prev    = closes[-2] / divisor
                change_bps = round((current - prev) * 100, 1)
                points.append(YieldPoint(
                    maturity=maturity,
                    yield_pct=round(current, 3),
                    change_bps=change_bps,
                    label=label,
                ))
                yield_by_maturity[maturity] = current
            except Exception:
                continue

        y10 = yield_by_maturity.get("10yr", 0.0)
        y3m = yield_by_maturity.get("3mo", 0.0)
        spread = round((y10 - y3m) * 100, 1)
        is_inverted = y10 > 0 and y3m > 0 and y10 < y3m
        fetched_at = datetime.now().isoformat()

        curve = YieldCurve(
            points=points,
            is_inverted=is_inverted,
            spread_10y3m=spread,
            fetched_at=fetched_at,
        )

        set_econ_cache(
            _CACHE_TICKER + _CACHE_TYPE,
            {
                "points": [p.__dict__ for p in points],
                "is_inverted": is_inverted,
                "spread_10y3m": spread,
                "fetched_at": fetched_at,
            },
        )
        return curve

    except Exception as exc:
        return YieldCurve(fetched_at=datetime.now().isoformat(), error=str(exc))
