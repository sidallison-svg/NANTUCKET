"""
yfinance data wrapper for stocks and ETFs.

Uses Yahoo Finance via the yfinance library — no API key required, no rate
limits for personal use.  Results are cached in SQLite to keep things fast:

  - Quotes cached for 15 minutes
  - Historical data cached for 24 hours

This keeps the same StockQuote interface the rest of the app relies on.
"""

from __future__ import annotations

import dataclasses
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

# ──────────────────────────────────────────────────────────────────────────────
# Cache config
# ──────────────────────────────────────────────────────────────────────────────

QUOTE_CACHE_MINUTES = 15
HISTORY_CACHE_HOURS = 24


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
    volume: int = 0
    avg_volume: int = 0
    volume_ratio: float = 0.0     # volume / avg_volume  (>1.5 = "hot")
    market_cap: float = 0.0
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None   # Annual yield in %
    sector: str = ""
    industry: str = ""
    week_52_high: float = 0.0
    week_52_low: float = 0.0
    price_vs_52w_low_pct: float = 0.0
    ma_50: Optional[float] = None
    ma_200: Optional[float] = None
    above_50ma: bool = False
    above_200ma: bool = False
    change_1w: float = 0.0
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    asset_type: str = "stock"
    error: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# SQLite cache
# ──────────────────────────────────────────────────────────────────────────────

def _get_cache_conn():
    from nantucket.db.database import get_connection
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quote_cache (
            ticker    TEXT NOT NULL,
            data_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, data_type)
        )
    """)
    conn.commit()
    return conn


def _get_cached(ticker: str, data_type: str, max_age_minutes: int) -> Optional[dict]:
    try:
        conn = _get_cache_conn()
        row = conn.execute(
            """SELECT data_json, fetched_at FROM quote_cache
               WHERE ticker = ? AND data_type = ?""",
            (ticker.upper(), data_type),
        ).fetchone()
        if not row:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        if datetime.now() - fetched < timedelta(minutes=max_age_minutes):
            return json.loads(row["data_json"])
    except Exception:
        pass
    return None


def _set_cache(ticker: str, data_type: str, data: dict) -> None:
    try:
        conn = _get_cache_conn()
        conn.execute(
            """INSERT OR REPLACE INTO quote_cache (ticker, data_type, data_json, fetched_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (ticker.upper(), data_type, json.dumps(data)),
        )
        conn.commit()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Main quote function
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(ticker: str) -> StockQuote:
    """
    Fetch a complete quote for one ticker using yfinance (Yahoo Finance).
    No API key required. Results cached 15 minutes in SQLite.
    """
    ticker = ticker.upper().strip()

    cached = _get_cached(ticker, "yf_quote", QUOTE_CACHE_MINUTES)
    if cached:
        try:
            return StockQuote(**cached)
        except Exception:
            pass

    try:
        t = yf.Ticker(ticker)
        info = t.info

        # Yahoo returns a mostly-empty dict for invalid tickers
        current_price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("navPrice")      # for mutual funds
            or 0.0
        )
        if not current_price:
            return StockQuote(ticker=ticker, error=f"No data found for {ticker} — check the ticker symbol")

        prev_close = (
            info.get("previousClose")
            or info.get("regularMarketPreviousClose")
            or current_price
        )
        change = current_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        volume = int(info.get("regularMarketVolume") or info.get("volume") or 0)
        avg_volume = int(info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0)
        volume_ratio = (volume / avg_volume) if avg_volume > 0 else 0.0

        market_cap = float(info.get("marketCap") or 0)

        pe = info.get("trailingPE") or info.get("forwardPE")
        pe_ratio = float(pe) if pe and float(pe) > 0 else None

        pb = info.get("priceToBook")
        pb_ratio = float(pb) if pb and float(pb) > 0 else None

        eps_val = info.get("trailingEps")
        eps = float(eps_val) if eps_val is not None else None

        div_raw = info.get("dividendYield") or 0
        div_yield = float(div_raw) * 100 if div_raw else None

        sector   = info.get("sector", "") or ""
        industry = info.get("industry", "") or ""
        name     = info.get("shortName") or info.get("longName") or ticker

        week_52_high = float(info.get("fiftyTwoWeekHigh") or 0)
        week_52_low  = float(info.get("fiftyTwoWeekLow") or 0)
        vs_low = ((current_price - week_52_low) / week_52_low * 100) if week_52_low > 0 else 0.0

        ma_50  = info.get("fiftyDayAverage")
        ma_200 = info.get("twoHundredDayAverage")
        ma_50  = float(ma_50)  if ma_50  else None
        ma_200 = float(ma_200) if ma_200 else None

        rev_growth  = info.get("revenueGrowth")
        earn_growth = info.get("earningsGrowth")
        rev_growth  = float(rev_growth)  if rev_growth  is not None else None
        earn_growth = float(earn_growth) if earn_growth is not None else None

        change_1w = _get_weekly_change_cached(ticker)

        result = StockQuote(
            ticker=ticker,
            name=name,
            price=float(current_price),
            change=round(change, 4),
            change_pct=round(change_pct, 4),
            volume=volume,
            avg_volume=avg_volume,
            volume_ratio=round(volume_ratio, 4),
            market_cap=market_cap,
            pe_ratio=pe_ratio,
            pb_ratio=pb_ratio,
            eps=eps,
            dividend_yield=div_yield,
            sector=sector,
            industry=industry,
            week_52_high=week_52_high,
            week_52_low=week_52_low,
            price_vs_52w_low_pct=round(vs_low, 2),
            ma_50=ma_50,
            ma_200=ma_200,
            above_50ma=(float(current_price) > ma_50) if ma_50 else False,
            above_200ma=(float(current_price) > ma_200) if ma_200 else False,
            change_1w=change_1w,
            revenue_growth=rev_growth,
            earnings_growth=earn_growth,
            asset_type="stock",
        )

        _set_cache(ticker, "yf_quote", dataclasses.asdict(result))
        return result

    except Exception as e:
        return StockQuote(ticker=ticker, error=str(e))


def _get_weekly_change_cached(ticker: str) -> float:
    """Get 1-week price change from cached 1-month history."""
    try:
        hist = get_history(ticker, period="1mo")
        if not hist.empty and len(hist) >= 6:
            start = float(hist["Close"].iloc[-6])
            end   = float(hist["Close"].iloc[-1])
            return round(((end - start) / start * 100), 2) if start else 0.0
    except Exception:
        pass
    return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Batch fetch
# ──────────────────────────────────────────────────────────────────────────────

def get_quotes_batch(
    tickers: list[str],
    max_workers: int = 5,
    progress_callback=None,
) -> dict[str, StockQuote]:
    """Fetch quotes for many tickers in parallel."""
    results: dict[str, StockQuote] = {}
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_ticker = {pool.submit(get_quote, t): t for t in tickers}
        for future in as_completed(future_to_ticker):
            t = future_to_ticker[future]
            try:
                results[t] = future.result()
            except Exception as exc:
                results[t] = StockQuote(ticker=t, error=str(exc))
            done += 1
            if progress_callback:
                progress_callback(done, len(tickers))

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Historical data for charts
# ──────────────────────────────────────────────────────────────────────────────

def get_history(
    ticker: str,
    period: str = "1y",
    outputsize: str = "full",   # kept for API compatibility, unused
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Get daily OHLCV price history from Yahoo Finance.
    Cached 24 hours in SQLite.

    period: "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"
    """
    cache_key = f"yf_history_{period}_{interval}"
    cached = _get_cached(ticker, cache_key, HISTORY_CACHE_HOURS * 60)
    if cached:
        try:
            df = pd.DataFrame(cached["data"])
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            return df
        except Exception:
            pass

    try:
        t = yf.Ticker(ticker.upper())
        df = t.history(period=period, interval=interval, auto_adjust=True)

        if df.empty:
            return pd.DataFrame()

        # Keep standard columns, strip timezone from index
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index

        _set_cache(ticker, cache_key, {"data": df.to_dict()})
        return df

    except Exception:
        return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────────────
# Screener universe
# ──────────────────────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    """
    Fetch S&P 500 tickers from Wikipedia.
    Falls back to hardcoded top-100 if unavailable.
    """
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        return [t for t in tickers if isinstance(t, str)]
    except Exception:
        return list(TOP_100_SP500)


# Curated top-100 S&P 500 by approximate market cap (2024).
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
