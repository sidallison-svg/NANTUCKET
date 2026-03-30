"""
yfinance wrapper for stocks and ETFs.

yfinance is a Python library that fetches data from Yahoo Finance.
Yahoo Finance is free and covers US stocks, ETFs, and many international markets.

Key concepts in this file:
- StockQuote: a dataclass that holds all the metrics for one ticker
- get_quote(): fetches a single ticker's current data
- get_quotes_batch(): fetches multiple tickers in parallel (much faster)
- get_history(): fetches OHLCV candles for charting
- get_sp500_tickers(): loads the S&P 500 universe for screening

Why use a dataclass?
A dataclass is like a struct — it's just a container for data with named fields.
Using `Optional[float]` for metrics like P/E means "this might be None" because
ETFs don't have P/E ratios and some stocks haven't reported earnings yet.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class StockQuote:
    """
    All the key metrics for a single stock/ETF at a point in time.

    Fields marked Optional[float] can be None — we handle that in the
    screener and display layers rather than crashing on missing data.
    """
    ticker: str
    name: str = ""
    price: float = 0.0
    change: float = 0.0           # Dollar change from previous close
    change_pct: float = 0.0       # Percent change from previous close
    volume: int = 0               # Today's volume so far
    avg_volume: int = 0           # 3-month average daily volume
    volume_ratio: float = 0.0     # volume / avg_volume  (>1.5 = "hot")
    market_cap: float = 0.0       # Total market value in dollars
    pe_ratio: Optional[float] = None    # Price / Earnings (trailing 12mo)
    pb_ratio: Optional[float] = None    # Price / Book value
    eps: Optional[float] = None         # Earnings per share (trailing 12mo)
    dividend_yield: Optional[float] = None   # Annual dividend yield in %
    sector: str = ""
    industry: str = ""
    week_52_high: float = 0.0
    week_52_low: float = 0.0
    price_vs_52w_low_pct: float = 0.0   # % above 52-week low (0 = AT the low)
    ma_50: Optional[float] = None       # 50-day moving average
    ma_200: Optional[float] = None      # 200-day moving average
    above_50ma: bool = False            # Is price above the 50-day MA?
    above_200ma: bool = False           # Is price above the 200-day MA?
    change_1w: float = 0.0              # % change over the past 5 trading days
    revenue_growth: Optional[float] = None    # YoY revenue growth (as decimal, e.g. 0.15 = 15%)
    earnings_growth: Optional[float] = None   # YoY earnings growth
    asset_type: str = "stock"           # stock | etf | crypto | future
    error: Optional[str] = None         # Set if something went wrong fetching data


# ──────────────────────────────────────────────────────────────────────────────
# Single-ticker fetch
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(ticker: str) -> StockQuote:
    """
    Fetch a complete quote for one ticker.

    yfinance's Ticker.info dict contains ~100+ fields from Yahoo Finance.
    We pull out the ones we care about and map them to our StockQuote.

    Error handling strategy: if ANY part fails, we return a StockQuote with
    the error field set rather than raising an exception. This lets the caller
    decide how to handle it (skip in screener, show warning in watchlist, etc.)
    """
    ticker = ticker.upper().strip()
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        # Yahoo sometimes returns a nearly-empty dict for invalid tickers
        price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("navPrice")  # for some ETFs
            or 0.0
        )
        if price == 0.0:
            # Last resort: try fast_info (lighter-weight API call)
            try:
                fi = t.fast_info
                price = fi.last_price or 0.0
                prev = fi.previous_close or price
                return StockQuote(
                    ticker=ticker,
                    name=info.get("longName") or info.get("shortName") or ticker,
                    price=price,
                    change=price - prev,
                    change_pct=((price - prev) / prev * 100) if prev else 0.0,
                )
            except Exception:
                return StockQuote(ticker=ticker, error=f"No price data found for {ticker}")

        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose") or price
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        # Volume
        volume = int(info.get("regularMarketVolume") or info.get("volume") or 0)
        avg_volume = int(info.get("averageVolume") or info.get("averageDailyVolume3Month") or 1)
        volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0

        # 52-week range
        hi52 = info.get("fiftyTwoWeekHigh") or 0.0
        lo52 = info.get("fiftyTwoWeekLow") or 0.0
        vs_low = ((price - lo52) / lo52 * 100) if lo52 > 0 else 0.0

        # Moving averages
        ma50 = info.get("fiftyDayAverage")
        ma200 = info.get("twoHundredDayAverage")

        # 1-week price change (requires a short history call)
        change_1w = _get_weekly_change(t)

        # Determine whether this is a stock or ETF
        quote_type = (info.get("quoteType") or "EQUITY").upper()
        if quote_type == "ETF":
            asset_type = "etf"
        elif quote_type in ("CRYPTOCURRENCY",):
            asset_type = "crypto"
        else:
            asset_type = "stock"

        # Dividend yield comes as a decimal (0.015 = 1.5%), convert to %
        raw_yield = info.get("dividendYield") or info.get("yield") or 0.0
        div_yield = raw_yield * 100 if raw_yield < 1.0 else raw_yield  # handle both formats

        return StockQuote(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName") or ticker,
            price=price,
            change=change,
            change_pct=change_pct,
            volume=volume,
            avg_volume=avg_volume,
            volume_ratio=volume_ratio,
            market_cap=info.get("marketCap") or 0,
            pe_ratio=info.get("trailingPE") or info.get("forwardPE"),
            pb_ratio=info.get("priceToBook"),
            eps=info.get("trailingEps"),
            dividend_yield=div_yield if div_yield > 0 else None,
            sector=info.get("sector") or "",
            industry=info.get("industry") or "",
            week_52_high=hi52,
            week_52_low=lo52,
            price_vs_52w_low_pct=vs_low,
            ma_50=ma50,
            ma_200=ma200,
            above_50ma=(price > ma50) if ma50 else False,
            above_200ma=(price > ma200) if ma200 else False,
            change_1w=change_1w,
            revenue_growth=info.get("revenueGrowth"),
            earnings_growth=info.get("earningsGrowth"),
            asset_type=asset_type,
        )

    except Exception as exc:
        return StockQuote(ticker=ticker, error=str(exc))


def _get_weekly_change(t: yf.Ticker) -> float:
    """Calculate price change over the past ~5 trading days."""
    try:
        hist = t.history(period="10d", interval="1d")
        if len(hist) >= 2:
            # Use 6 periods back if available, otherwise just first vs last
            idx = min(6, len(hist) - 1)
            start = hist["Close"].iloc[-idx - 1]
            end = hist["Close"].iloc[-1]
            return ((end - start) / start * 100) if start else 0.0
    except Exception:
        pass
    return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Batch fetch (parallel using threads)
# ──────────────────────────────────────────────────────────────────────────────

def get_quotes_batch(
    tickers: list[str],
    max_workers: int = 15,
    progress_callback=None,
) -> dict[str, StockQuote]:
    """
    Fetch quotes for many tickers in parallel using a thread pool.

    Why threads and not async/await?
    yfinance uses requests under the hood (synchronous HTTP). Running requests
    in threads is the right tool here — Python's GIL releases during I/O, so
    thread-based parallelism works great for network calls.

    Args:
        tickers: List of ticker symbols
        max_workers: How many threads to run at once (15 is a safe default
                     that avoids Yahoo Finance rate limits)
        progress_callback: Optional callable(done, total) for progress bars

    Returns:
        Dict mapping ticker → StockQuote
    """
    results: dict[str, StockQuote] = {}
    total = len(tickers)
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_ticker = {pool.submit(get_quote, t): t for t in tickers}
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                results[ticker] = future.result()
            except Exception as exc:
                results[ticker] = StockQuote(ticker=ticker, error=str(exc))
            done += 1
            if progress_callback:
                progress_callback(done, total)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Historical data for charts
# ──────────────────────────────────────────────────────────────────────────────

def get_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Get OHLCV (Open/High/Low/Close/Volume) candle data.

    Common periods: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max
    Common intervals: 1m, 5m, 15m, 1h, 1d, 1wk, 1mo

    Returns an empty DataFrame on error rather than raising.
    """
    try:
        t = yf.Ticker(ticker.upper())
        df = t.history(period=period, interval=interval)
        return df
    except Exception:
        return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────────────
# Screener universe
# ──────────────────────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    """
    Fetch the current S&P 500 constituent list from Wikipedia.

    Wikipedia keeps this table updated when companies are added/removed from
    the index. If the Wikipedia fetch fails (e.g., no internet, page changed),
    we fall back to a hardcoded top-100 list.
    """
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        df = tables[0]
        # Yahoo Finance uses '-' not '.' in tickers (e.g. BRK-B not BRK.B)
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        return [t for t in tickers if isinstance(t, str)]
    except Exception:
        return list(TOP_100_SP500)


# Curated top-100 S&P 500 by approximate market cap (2024).
# Used as the fast default screening universe and as a fallback.
TOP_100_SP500: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "TSLA", "LLY",
    "AVGO", "JPM", "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST",
    "MRK", "ABBV", "CVX", "CRM", "BAC", "KO", "PEP", "AMD", "NFLX", "ADBE",
    "TMO", "WMT", "ACN", "MCD", "CSCO", "ABT", "LIN", "DHR", "TXN", "NKE",
    "PM", "NEE", "ORCL", "INTC", "CMCSA", "VZ", "INTU", "RTX", "AMGN", "HON",
    "IBM", "QCOM", "BMY", "UPS", "LOW", "CAT", "ELV", "DE", "SPGI", "GS",
    "MS", "ISRG", "MDT", "AXP", "BLK", "GILD", "T", "PLD", "CI", "REGN",
    "ADI", "VRTX", "MDLZ", "TJX", "MO", "DUK", "ETN", "SYK", "ZTS", "LRCX",
    "MMC", "CB", "AON", "BSX", "ITW", "PGR", "APH", "SO", "CL", "WM",
    "GE", "KLAC", "SNPS", "CDNS", "HCA", "FI", "EQIX", "NSC", "MCO", "ADP",
)
