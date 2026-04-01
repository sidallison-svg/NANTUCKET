"""
Stock/ETF screener — the core filtering engine.

How it works:
1. Start with a universe of tickers (default: top 100 S&P 500 for speed,
   or full S&P 500 with --universe sp500)
2. Fetch quotes in parallel using get_quotes_batch()
3. Apply filters to each quote
4. Sort by a key metric
5. Return the top N results

Filter philosophy:
- All filters are optional — if you don't specify one, it's ignored
- Filters combine with AND logic (a stock must pass ALL filters)
- None values (missing data) automatically FAIL a filter — we only
  show stocks where we can confirm the filter condition

Preset system:
- Presets are JSON objects stored in presets/default_screens.json
- Users can save their own presets via the CLI
- Custom filter flags override preset values
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from nantucket.data.stocks import StockQuote, get_quotes_batch, get_sp500_tickers, TOP_100_SP500

# Path to the built-in presets file
PRESETS_PATH = Path(__file__).parent.parent / "presets" / "default_screens.json"


# ──────────────────────────────────────────────────────────────────────────────
# Filter specification
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScreenFilters:
    """
    All available filter parameters for the screener.
    Every field is Optional — None means "don't filter on this".

    Usage example:
        filters = ScreenFilters(pe_max=15, pb_max=1.5, eps_min=0)
        results = run_screen(filters)
    """
    # Valuation
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pb_min: Optional[float] = None
    pb_max: Optional[float] = None
    eps_min: Optional[float] = None

    # Size
    market_cap_min: Optional[float] = None
    market_cap_max: Optional[float] = None

    # Volume
    volume_min: Optional[float] = None
    volume_ratio_min: Optional[float] = None    # volume / avg_volume

    # Income
    div_yield_min: Optional[float] = None       # in percent, e.g. 3.0 = 3%

    # Price action
    change_pct_min: Optional[float] = None
    change_pct_max: Optional[float] = None
    change_1w_min: Optional[float] = None

    # Technical
    above_50ma: Optional[bool] = None
    above_200ma: Optional[bool] = None
    near_52w_low: Optional[float] = None        # within N% of 52-week low

    # Fundamental growth
    revenue_growth_min: Optional[float] = None  # as decimal, 0.20 = 20%
    earnings_growth_min: Optional[float] = None

    # Classification
    sector: Optional[str] = None

    # Display controls
    sort_by: str = "market_cap"
    sort_order: str = "desc"          # "asc" or "desc"
    limit: int = 10

    # Asset type filter
    asset_types: list[str] = field(default_factory=lambda: ["stock", "etf"])

    @classmethod
    def from_dict(cls, d: dict) -> "ScreenFilters":
        """Build a ScreenFilters from a plain dict (e.g. from JSON preset)."""
        valid_fields = cls.__dataclass_fields__.keys()
        kwargs = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**kwargs)

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dict (for saving custom screens)."""
        import dataclasses
        return {
            k: v for k, v in dataclasses.asdict(self).items()
            if v is not None and k not in ("limit",)
        }


# ──────────────────────────────────────────────────────────────────────────────
# Screen result
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScreenResult:
    """The output of a screen run."""
    quotes: list[StockQuote]
    filters_used: ScreenFilters
    universe_size: int      # how many tickers we started with
    errors: int             # how many tickers had fetch errors
    preset_name: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Preset management
# ──────────────────────────────────────────────────────────────────────────────

def load_presets() -> dict:
    """Load all built-in presets from default_screens.json."""
    if PRESETS_PATH.exists():
        with open(PRESETS_PATH) as f:
            data = json.load(f)
        # Strip metadata keys that start with '_'
        return {k: v for k, v in data.items() if not k.startswith("_")}
    return {}


def get_preset(name: str) -> Optional[ScreenFilters]:
    """
    Look up a preset by name and return it as a ScreenFilters object.
    Returns None if the preset doesn't exist.
    """
    presets = load_presets()
    if name not in presets:
        return None
    p = presets[name]
    filters_dict = p.get("filters", {})
    filters_dict["sort_by"] = p.get("sort_by", "market_cap")
    filters_dict["sort_order"] = p.get("sort_order", "desc")
    if "asset_types" in p:
        filters_dict["asset_types"] = p["asset_types"]
    return ScreenFilters.from_dict(filters_dict)


def list_presets() -> dict[str, str]:
    """Return a dict of {preset_name: description} for display."""
    presets = load_presets()
    return {name: data.get("description", "") for name, data in presets.items()}


def get_preset_learning_note(name: str) -> Optional[str]:
    """Return the educational note for a preset, if any."""
    presets = load_presets()
    return presets.get(name, {}).get("learning_note")


# ──────────────────────────────────────────────────────────────────────────────
# Filtering logic
# ──────────────────────────────────────────────────────────────────────────────

def _passes_filters(q: StockQuote, f: ScreenFilters) -> bool:
    """
    Check if a StockQuote passes all active filters.

    Each filter is checked only if it's not None.
    If a filter is active but the quote is missing that data (None), the quote fails.
    """
    if q.error:
        return False

    # Asset type
    if q.asset_type not in f.asset_types:
        return False

    # ── Valuation ────────────────────────────────────────────────────────────
    if f.pe_min is not None:
        if q.pe_ratio is None or q.pe_ratio < f.pe_min:
            return False
    if f.pe_max is not None:
        if q.pe_ratio is None or q.pe_ratio <= 0 or q.pe_ratio > f.pe_max:
            return False
    if f.pb_min is not None:
        if q.pb_ratio is None or q.pb_ratio < f.pb_min:
            return False
    if f.pb_max is not None:
        if q.pb_ratio is None or q.pb_ratio <= 0 or q.pb_ratio > f.pb_max:
            return False
    if f.eps_min is not None:
        if q.eps is None or q.eps < f.eps_min:
            return False

    # ── Size ─────────────────────────────────────────────────────────────────
    if f.market_cap_min is not None and q.market_cap < f.market_cap_min:
        return False
    if f.market_cap_max is not None and q.market_cap > f.market_cap_max:
        return False

    # ── Volume ───────────────────────────────────────────────────────────────
    if f.volume_min is not None and q.volume < f.volume_min:
        return False
    if f.volume_ratio_min is not None and q.volume_ratio < f.volume_ratio_min:
        return False

    # ── Income ───────────────────────────────────────────────────────────────
    if f.div_yield_min is not None:
        if q.dividend_yield is None or q.dividend_yield < f.div_yield_min:
            return False

    # ── Price action ─────────────────────────────────────────────────────────
    if f.change_pct_min is not None and q.change_pct < f.change_pct_min:
        return False
    if f.change_pct_max is not None and q.change_pct > f.change_pct_max:
        return False
    if f.change_1w_min is not None and q.change_1w < f.change_1w_min:
        return False

    # ── Technical ────────────────────────────────────────────────────────────
    if f.above_50ma is True and not q.above_50ma:
        return False
    if f.above_200ma is True and not q.above_200ma:
        return False
    if f.near_52w_low is not None:
        if q.price_vs_52w_low_pct > f.near_52w_low:
            return False

    # ── Fundamental growth ───────────────────────────────────────────────────
    if f.revenue_growth_min is not None:
        if q.revenue_growth is None or q.revenue_growth < f.revenue_growth_min:
            return False
    if f.earnings_growth_min is not None:
        if q.earnings_growth is None or q.earnings_growth < f.earnings_growth_min:
            return False

    # ── Sector ───────────────────────────────────────────────────────────────
    if f.sector:
        if not q.sector or f.sector.lower() not in q.sector.lower():
            return False

    return True


def _sort_key(q: StockQuote, sort_by: str) -> float:
    """Extract the numeric sort key from a quote, defaulting to 0 for None."""
    mapping = {
        "market_cap":         q.market_cap,
        "pe_ratio":           q.pe_ratio,
        "pb_ratio":           q.pb_ratio,
        "eps":                q.eps,
        "change_pct":         q.change_pct,
        "change_1w":          q.change_1w,
        "volume":             float(q.volume),
        "volume_ratio":       q.volume_ratio,
        "dividend_yield":     q.dividend_yield,
        "price_vs_52w_low":   q.price_vs_52w_low_pct,
        "price":              q.price,
        "revenue_growth":     q.revenue_growth,
        "earnings_growth":    q.earnings_growth,
    }
    val = mapping.get(sort_by, q.market_cap)
    return val if val is not None else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Main screening function
# ──────────────────────────────────────────────────────────────────────────────

def run_screen(
    filters: ScreenFilters,
    universe: str = "top100",   # "top100" | "sp500" | list of tickers
    tickers: Optional[list[str]] = None,
    show_progress: bool = True,
) -> ScreenResult:
    """
    Run a stock screen against the given universe.

    Args:
        filters: ScreenFilters specifying what to look for
        universe: "top100" (fast, ~30s), "sp500" (thorough, ~3-5min),
                  or "custom" if tickers is provided
        tickers: Explicit list of tickers to screen (overrides universe)
        show_progress: Show a Rich progress bar while fetching data

    Returns:
        ScreenResult with matching quotes sorted by filters.sort_by
    """
    # Build the ticker list
    if tickers:
        ticker_list = [t.upper() for t in tickers]
    elif universe == "sp500":
        ticker_list = get_sp500_tickers()
    else:
        ticker_list = list(TOP_100_SP500)

    universe_size = len(ticker_list)
    quotes_dict: dict[str, StockQuote] = {}

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]Fetching market data..."),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[dim]{task.completed}/{task.total} tickers"),
            transient=True,
        ) as progress:
            task = progress.add_task("Screening", total=universe_size)

            def on_progress(done: int, total: int) -> None:
                progress.update(task, completed=done)

            quotes_dict = get_quotes_batch(
                ticker_list,
                max_workers=15,
                progress_callback=on_progress,
                with_fundamentals=True,
            )
    else:
        quotes_dict = get_quotes_batch(ticker_list, max_workers=15, with_fundamentals=True)

    # Count errors
    errors = sum(1 for q in quotes_dict.values() if q.error)

    # Apply filters
    passing = [q for q in quotes_dict.values() if _passes_filters(q, filters)]

    # Sort
    reverse = (filters.sort_order == "desc")
    passing.sort(key=lambda q: _sort_key(q, filters.sort_by), reverse=reverse)

    # Take top N
    top = passing[: filters.limit]

    return ScreenResult(
        quotes=top,
        filters_used=filters,
        universe_size=universe_size,
        errors=errors,
    )
