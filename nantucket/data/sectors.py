"""
S&P 500 sector ETF performance heatmap.

Uses the 11 GICS sector ETFs from State Street (SPDR). These are the
standard sector benchmarks used by Bloomberg and most institutional desks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# (ETF ticker, sector name)
SECTOR_ETFS: list[tuple[str, str]] = [
    ("XLK",  "Technology"),
    ("XLF",  "Financials"),
    ("XLE",  "Energy"),
    ("XLV",  "Health Care"),
    ("XLI",  "Industrials"),
    ("XLB",  "Materials"),
    ("XLRE", "Real Estate"),
    ("XLY",  "Consumer Disc."),
    ("XLP",  "Consumer Staples"),
    ("XLU",  "Utilities"),
    ("XLC",  "Communication"),
]

_CACHE_KEY = "__SECTORS__sector_heatmap"
_CACHE_TTL = 15  # minutes


@dataclass
class SectorPerformance:
    ticker: str
    name: str
    change_1d: float
    change_1w: float
    change_1mo: float
    price: float
    error: Optional[str] = None


@dataclass
class SectorHeatmap:
    sectors: list[SectorPerformance] = field(default_factory=list)
    fetched_at: str = ""
    error: Optional[str] = None


def get_sector_heatmap() -> SectorHeatmap:
    """Fetch sector ETF performance with caching."""
    from nantucket.db.database import get_econ_cached, set_econ_cache
    from nantucket.data.stocks import get_quotes_batch, get_history

    cached = get_econ_cached(_CACHE_KEY, _CACHE_TTL)
    if cached:
        sectors = [SectorPerformance(**s) for s in cached["sectors"]]
        return SectorHeatmap(sectors=sectors, fetched_at=cached["fetched_at"])

    try:
        tickers = [t[0] for t in SECTOR_ETFS]
        ticker_to_name = dict(SECTOR_ETFS)

        quotes = get_quotes_batch(tickers, max_workers=5)

        sectors: list[SectorPerformance] = []
        for ticker, name in SECTOR_ETFS:
            q = quotes.get(ticker)
            if q is None or q.error:
                sectors.append(SectorPerformance(
                    ticker=ticker, name=name,
                    change_1d=0.0, change_1w=0.0, change_1mo=0.0,
                    price=0.0, error="No data",
                ))
                continue

            # 1-week change from quote object; 1-month from history
            change_1w = q.change_1w or 0.0
            change_1mo = 0.0
            try:
                hist = get_history(ticker, period="1mo", outputsize="compact")
                if hist is not None and len(hist) >= 2:
                    start_px = float(hist["Close"].iloc[0])
                    end_px = float(hist["Close"].iloc[-1])
                    if start_px > 0:
                        change_1mo = round((end_px - start_px) / start_px * 100, 2)
            except Exception:
                pass

            sectors.append(SectorPerformance(
                ticker=ticker,
                name=name,
                change_1d=round(q.change_pct or 0.0, 2),
                change_1w=round(change_1w, 2),
                change_1mo=round(change_1mo, 2),
                price=round(q.price or 0.0, 2),
            ))

        # Sort by today's change descending
        sectors.sort(key=lambda s: s.change_1d, reverse=True)
        fetched_at = datetime.now().isoformat()

        set_econ_cache(_CACHE_KEY, {
            "sectors": [s.__dict__ for s in sectors],
            "fetched_at": fetched_at,
        })

        return SectorHeatmap(sectors=sectors, fetched_at=fetched_at)

    except Exception as exc:
        return SectorHeatmap(fetched_at=datetime.now().isoformat(), error=str(exc))
