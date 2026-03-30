"""
Futures and commodities data via yfinance.

Yahoo Finance uses a specific ticker format for futures:
- Continuous contracts: GC=F (Gold), CL=F (Crude Oil), SI=F (Silver), etc.
  These are front-month contracts that roll over automatically.
- "=F" suffix = futures contract

Why futures?
Commodities like gold, oil, and agricultural products are a key part of macro
analysis. When oil spikes, energy sector stocks typically follow. When gold
rallies, it often signals fear/uncertainty in equity markets.

Important: Futures prices are quoted differently from stocks.
- Gold (GC=F) is in USD per troy ounce
- Crude Oil (CL=F) is in USD per barrel
- Natural Gas (NG=F) is in USD per MMBtu (million British thermal units)
- Corn/Wheat (ZC=F, ZW=F) are in USD per bushel (but quoted in cents in raw data)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from nantucket.data.stocks import StockQuote, get_quote


# Known futures tickers with human-readable names and units
FUTURES_UNIVERSE: dict[str, dict] = {
    "GC=F":  {"name": "Gold",         "unit": "$/troy oz",   "category": "Metals"},
    "SI=F":  {"name": "Silver",        "unit": "$/troy oz",   "category": "Metals"},
    "HG=F":  {"name": "Copper",        "unit": "$/lb",        "category": "Metals"},
    "PL=F":  {"name": "Platinum",      "unit": "$/troy oz",   "category": "Metals"},
    "CL=F":  {"name": "Crude Oil WTI", "unit": "$/barrel",    "category": "Energy"},
    "BZ=F":  {"name": "Brent Crude",   "unit": "$/barrel",    "category": "Energy"},
    "NG=F":  {"name": "Natural Gas",   "unit": "$/MMBtu",     "category": "Energy"},
    "RB=F":  {"name": "Gasoline",      "unit": "$/gallon",    "category": "Energy"},
    "ZC=F":  {"name": "Corn",          "unit": "cents/bushel","category": "Agriculture"},
    "ZW=F":  {"name": "Wheat",         "unit": "cents/bushel","category": "Agriculture"},
    "ZS=F":  {"name": "Soybeans",      "unit": "cents/bushel","category": "Agriculture"},
    "KC=F":  {"name": "Coffee",        "unit": "cents/lb",    "category": "Agriculture"},
    "CT=F":  {"name": "Cotton",        "unit": "cents/lb",    "category": "Agriculture"},
    "ES=F":  {"name": "S&P 500 E-Mini","unit": "index pts",   "category": "Equity Index"},
    "NQ=F":  {"name": "Nasdaq 100 E-Mini","unit": "index pts","category": "Equity Index"},
    "YM=F":  {"name": "Dow Jones E-Mini","unit": "index pts", "category": "Equity Index"},
    "RTY=F": {"name": "Russell 2000",  "unit": "index pts",   "category": "Equity Index"},
    "ZB=F":  {"name": "US 30-Year Bond","unit": "% of par",   "category": "Rates"},
    "ZN=F":  {"name": "US 10-Year Note","unit": "% of par",   "category": "Rates"},
    "ZT=F":  {"name": "US 2-Year Note", "unit": "% of par",   "category": "Rates"},
    "6E=F":  {"name": "EUR/USD",       "unit": "USD per EUR", "category": "FX"},
    "6J=F":  {"name": "JPY/USD",       "unit": "USD per 100Y","category": "FX"},
    "6B=F":  {"name": "GBP/USD",       "unit": "USD per GBP", "category": "FX"},
}

# Shortcut aliases (so users can type "GOLD" instead of "GC=F")
ALIASES: dict[str, str] = {
    "GOLD":   "GC=F",
    "SILVER": "SI=F",
    "COPPER": "HG=F",
    "OIL":    "CL=F",
    "CRUDE":  "CL=F",
    "BRENT":  "BZ=F",
    "GAS":    "NG=F",
    "NATGAS": "NG=F",
    "CORN":   "ZC=F",
    "WHEAT":  "ZW=F",
    "SOY":    "ZS=F",
    "COFFEE": "KC=F",
    "SP500":  "ES=F",
    "NQ100":  "NQ=F",
    "DOW":    "YM=F",
}


def resolve_futures_ticker(ticker: str) -> str:
    """Convert an alias like 'GOLD' to the Yahoo Finance symbol 'GC=F'."""
    upper = ticker.upper()
    return ALIASES.get(upper, upper)


def get_futures_quote(ticker: str) -> StockQuote:
    """
    Fetch a futures/commodity quote.

    Returns a StockQuote (same dataclass as stocks) with asset_type='future'.
    This lets the rest of the app treat futures uniformly with stocks/ETFs.
    """
    resolved = resolve_futures_ticker(ticker)
    meta = FUTURES_UNIVERSE.get(resolved, {})

    quote = get_quote(resolved)
    quote.asset_type = "future"

    # Override name with our friendly name if we have one
    if meta.get("name"):
        quote.name = f"{meta['name']} ({meta.get('unit', '')})"
        quote.sector = meta.get("category", "Commodity")

    return quote


def get_all_futures() -> list[StockQuote]:
    """Fetch quotes for all known futures contracts."""
    from nantucket.data.stocks import get_quotes_batch
    tickers = list(FUTURES_UNIVERSE.keys())
    quotes_dict = get_quotes_batch(tickers, max_workers=8)

    results = []
    for ticker, quote in quotes_dict.items():
        quote.asset_type = "future"
        meta = FUTURES_UNIVERSE.get(ticker, {})
        if meta.get("name"):
            quote.name = f"{meta['name']} ({meta.get('unit', '')})"
            quote.sector = meta.get("category", "Commodity")
        results.append(quote)

    return results


def get_futures_by_category(category: str) -> list[StockQuote]:
    """Fetch futures for a specific category: Metals, Energy, Agriculture, etc."""
    tickers = [
        sym for sym, meta in FUTURES_UNIVERSE.items()
        if meta.get("category", "").lower() == category.lower()
    ]
    if not tickers:
        return []
    from nantucket.data.stocks import get_quotes_batch
    quotes_dict = get_quotes_batch(tickers, max_workers=8)
    return list(quotes_dict.values())
