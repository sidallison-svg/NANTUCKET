"""
Alpha Vantage data wrapper for stocks and ETFs.

Alpha Vantage is a free stock data API that works reliably without SSL issues.
Free tier: 25 requests/day — we make this work with smart SQLite caching:
  - Quotes cached for 15 minutes (so re-running watch show is free)
  - Fundamentals (P/E, sector, etc.) cached for 24 hours
  - History cached for 24 hours

Setup: Set ALPHA_VANTAGE_KEY in your .env file.
Get a free key at: https://www.alphavantage.co/support/#api-key

API docs: https://www.alphavantage.co/documentation/
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

AV_BASE = "https://www.alphavantage.co/query"
QUOTE_CACHE_MINUTES = 15      # How long to cache price quotes
OVERVIEW_CACHE_HOURS = 24     # How long to cache fundamentals
HISTORY_CACHE_HOURS = 24      # How long to cache price history
YF_FUNDAMENTALS_CACHE_HOURS = 7 * 24   # Cache yfinance fundamentals 7 days


def _get_api_key() -> str:
    key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if not key:
        raise RuntimeError(
            "ALPHA_VANTAGE_KEY not set!\n"
            "Add it to your .env file: ALPHA_VANTAGE_KEY=your-key\n"
            "Get a free key at: https://www.alphavantage.co/support/#api-key"
        )
    return key


# ──────────────────────────────────────────────────────────────────────────────
# Data model (same interface as before — nothing else in the app changes)
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
    avg_volume: int = 0           # Average daily volume
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
    price_vs_52w_low_pct: float = 0.0   # % above 52-week low
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
# SQLite cache — avoids burning API requests on repeated calls
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
    """Return cached data if it's fresh enough, otherwise None."""
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
        age = datetime.now() - fetched
        if age < timedelta(minutes=max_age_minutes):
            return json.loads(row["data_json"])
    except Exception:
        pass
    return None


def _set_cache(ticker: str, data_type: str, data: dict) -> None:
    """Store data in the cache."""
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
# Alpha Vantage API calls
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_global_quote(ticker: str) -> Optional[dict]:
    """
    Fetch current price, change, and volume from Alpha Vantage GLOBAL_QUOTE.
    Uses cache first — only hits the API if data is older than 15 minutes.
    """
    cached = _get_cached(ticker, "quote", QUOTE_CACHE_MINUTES)
    if cached:
        return cached

    try:
        resp = requests.get(AV_BASE, params={
            "function": "GLOBAL_QUOTE",
            "symbol": ticker,
            "apikey": _get_api_key(),
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        q = data.get("Global Quote", {})
        if not q or not q.get("05. price"):
            return None

        result = {
            "price":      float(q.get("05. price", 0)),
            "open":       float(q.get("02. open", 0)),
            "high":       float(q.get("03. high", 0)),
            "low":        float(q.get("04. low", 0)),
            "prev_close": float(q.get("08. previous close", 0)),
            "change":     float(q.get("09. change", 0)),
            "change_pct": float(q.get("10. change percent", "0%").replace("%", "")),
            "volume":     int(q.get("06. volume", 0)),
        }
        _set_cache(ticker, "quote", result)
        return result

    except Exception:
        return None


def _fetch_overview(ticker: str) -> Optional[dict]:
    """
    Fetch company fundamentals from Alpha Vantage OVERVIEW.
    Cached for 24 hours since fundamentals don't change often.
    Costs 1 API request.
    """
    cached = _get_cached(ticker, "overview", OVERVIEW_CACHE_HOURS * 60)
    if cached:
        return cached

    try:
        resp = requests.get(AV_BASE, params={
            "function": "OVERVIEW",
            "symbol": ticker,
            "apikey": _get_api_key(),
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data or "Symbol" not in data:
            return None

        def _f(key):
            v = data.get(key, "None")
            try:
                return float(v) if v and v != "None" else None
            except (ValueError, TypeError):
                return None

        result = {
            "name":            data.get("Name", ticker),
            "sector":          data.get("Sector", ""),
            "industry":        data.get("Industry", ""),
            "market_cap":      _f("MarketCapitalization") or 0,
            "pe_ratio":        _f("PERatio"),
            "pb_ratio":        _f("PriceToBookRatio"),
            "eps":             _f("EPS"),
            "dividend_yield":  (_f("DividendYield") or 0) * 100,
            "week_52_high":    _f("52WeekHigh") or 0,
            "week_52_low":     _f("52WeekLow") or 0,
            "ma_50":           _f("50DayMovingAverage"),
            "ma_200":          _f("200DayMovingAverage"),
            "revenue_growth":  _f("QuarterlyRevenueGrowthYOY"),
            "earnings_growth": _f("QuarterlyEarningsGrowthYOY"),
            "avg_volume":      int(_f("10DayAverageTradingVolume") or 0) * 1000,
        }
        _set_cache(ticker, "overview", result)
        return result

    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Main quote function
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(ticker: str) -> StockQuote:
    """
    Fetch a complete quote for one ticker using Alpha Vantage.

    Makes up to 2 API calls per ticker (quote + overview), but uses
    SQLite cache aggressively to stay within the 25 requests/day free limit.

    Cache strategy:
    - Price/volume: cached 15 minutes (fresh enough for trading decisions)
    - Fundamentals: cached 24 hours (P/E doesn't change minute to minute)
    """
    ticker = ticker.upper().strip()

    try:
        # Always get quote (cached 15 min)
        q = _fetch_global_quote(ticker)
        if not q:
            return StockQuote(ticker=ticker, error=f"No data found for {ticker} — check the ticker symbol")

        price = q["price"]
        prev_close = q["prev_close"]
        change = q["change"]
        change_pct = q["change_pct"]
        volume = q["volume"]

        # Get fundamentals (cached 24h) — best effort, not required
        ov = _fetch_overview(ticker) or {}

        avg_volume = ov.get("avg_volume") or 0
        volume_ratio = (volume / avg_volume) if avg_volume > 0 else 0.0

        week_52_high = ov.get("week_52_high") or 0.0
        week_52_low  = ov.get("week_52_low") or 0.0
        vs_low = ((price - week_52_low) / week_52_low * 100) if week_52_low > 0 else 0.0

        ma50  = ov.get("ma_50")
        ma200 = ov.get("ma_200")

        div_yield = ov.get("dividend_yield") or 0
        rev_growth = ov.get("revenue_growth")
        earn_growth = ov.get("earnings_growth")

        # Calculate 1-week change from cached history
        change_1w = _get_weekly_change_cached(ticker)

        return StockQuote(
            ticker=ticker,
            name=ov.get("name") or ticker,
            price=price,
            change=change,
            change_pct=change_pct,
            volume=volume,
            avg_volume=avg_volume,
            volume_ratio=volume_ratio,
            market_cap=ov.get("market_cap") or 0,
            pe_ratio=ov.get("pe_ratio"),
            pb_ratio=ov.get("pb_ratio"),
            eps=ov.get("eps"),
            dividend_yield=div_yield if div_yield > 0 else None,
            sector=ov.get("sector") or "",
            industry=ov.get("industry") or "",
            week_52_high=week_52_high,
            week_52_low=week_52_low,
            price_vs_52w_low_pct=vs_low,
            ma_50=ma50,
            ma_200=ma200,
            above_50ma=(price > ma50) if ma50 else False,
            above_200ma=(price > ma200) if ma200 else False,
            change_1w=change_1w,
            revenue_growth=rev_growth,
            earnings_growth=earn_growth,
            asset_type="stock",
        )

    except RuntimeError as e:
        return StockQuote(ticker=ticker, error=str(e))
    except Exception as e:
        return StockQuote(ticker=ticker, error=str(e))


def _get_weekly_change_cached(ticker: str) -> float:
    """Get 1-week price change using cached daily history."""
    try:
        hist = get_history(ticker, outputsize="compact")
        if not hist.empty and len(hist) >= 6:
            start = hist["Close"].iloc[-6]
            end   = hist["Close"].iloc[-1]
            return ((end - start) / start * 100) if start else 0.0
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
    """
    Fetch quotes for many tickers.

    Note: max_workers is kept low (5) to avoid hitting Alpha Vantage rate
    limits. Cached results don't count against the limit.
    """
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
            # Small delay between requests to be respectful of rate limits
            time.sleep(0.2)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Historical data for charts
# ──────────────────────────────────────────────────────────────────────────────

def get_history(
    ticker: str,
    period: str = "1y",
    outputsize: str = "full",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Get daily OHLCV price history from Alpha Vantage TIME_SERIES_DAILY.
    Cached for 24 hours to save API requests.

    Args:
        ticker: Stock symbol
        period: Not used directly (Alpha Vantage returns full/compact history)
        outputsize: "compact" = last 100 days, "full" = up to 20 years
    """
    cache_key = f"history_{outputsize}"
    cached = _get_cached(ticker, cache_key, HISTORY_CACHE_HOURS * 60)
    if cached:
        try:
            df = pd.DataFrame(cached["data"])
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            return _filter_by_period(df, period)
        except Exception:
            pass

    try:
        resp = requests.get(AV_BASE, params={
            "function":   "TIME_SERIES_DAILY",
            "symbol":     ticker.upper(),
            "outputsize": outputsize,
            "apikey":     _get_api_key(),
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        ts = data.get("Time Series (Daily)", {})
        if not ts:
            return pd.DataFrame()

        rows = []
        for date_str, vals in ts.items():
            rows.append({
                "Date":   date_str,
                "Open":   float(vals["1. open"]),
                "High":   float(vals["2. high"]),
                "Low":    float(vals["3. low"]),
                "Close":  float(vals["4. close"]),
                "Volume": int(vals["5. volume"]),
            })

        df = pd.DataFrame(rows).set_index("Date")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # Cache the raw data
        _set_cache(ticker, cache_key, {"data": df.to_dict()})

        return _filter_by_period(df, period)

    except Exception:
        return pd.DataFrame()


def _filter_by_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Filter a DataFrame to a given period string like '1y', '6mo', '3mo'."""
    if df.empty:
        return df
    period_map = {
        "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
        "6mo": 180, "1y": 365, "2y": 730, "5y": 1825,
    }
    days = period_map.get(period, 365)
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
    return df[df.index >= cutoff]


# ──────────────────────────────────────────────────────────────────────────────
# Screener universe
# ──────────────────────────────────────────────────────────────────────────────

def _yf_float(val) -> Optional[float]:
    """Safely convert a yfinance value to float, returning None on failure."""
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _yf_session():
    """Build a requests Session with browser headers to avoid Yahoo bot detection."""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return sess


def _fetch_yf_fundamentals(ticker: str, sess=None) -> tuple[str, Optional[dict]]:
    """
    Fetch fundamentals for one ticker via yfinance.info.
    Cached for 7 days — PE/sector rarely change.
    Returns (ticker, dict) or (ticker, None) on any failure.
    """
    import yfinance as yf

    cached = _get_cached(ticker, "yf_fundamentals", YF_FUNDAMENTALS_CACHE_HOURS * 60)
    if cached:
        return ticker, cached

    for attempt in range(3):
        try:
            info = yf.Ticker(ticker, session=sess).info
            if not info or len(info) < 3:
                return ticker, None
            result = {
                "name":        info.get("shortName") or info.get("longName") or ticker,
                "sector":      info.get("sector") or "",
                "industry":    info.get("industry") or "",
                "market_cap":  float(info.get("marketCap") or 0),
                "pe_ratio":    _yf_float(info.get("trailingPE")),
                "pb_ratio":    _yf_float(info.get("priceToBook")),
                "eps":         _yf_float(info.get("trailingEps")),
                "div_yield":   (float(info.get("dividendYield") or 0)) * 100,
                "rev_growth":  _yf_float(info.get("revenueGrowth")),
                "earn_growth": _yf_float(info.get("earningsGrowth")),
            }
            _set_cache(ticker, "yf_fundamentals", result)
            return ticker, result
        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                time.sleep(2 ** (attempt + 2))  # 4s, 8s, 16s backoff
            else:
                break

    return ticker, None


def get_quotes_batch_yf(
    tickers: list[str],
    progress_callback=None,
) -> dict[str, StockQuote]:
    """
    Fetch quotes for many tickers using yfinance (free, no API key needed).

    Strategy:
    1. One bulk yf.download(period="1y") for all price/volume/MA/52wk data
    2. Sequential .info calls with 1s gap + browser headers + 7-day SQLite cache
       Sequential (not parallel) to avoid triggering Yahoo rate limits.

    First run: ~3-5 min for 100 stocks. After that, cached for 7 days.
    """
    import yfinance as yf

    results: dict[str, StockQuote] = {}
    total = len(tickers)
    sess = _yf_session()

    # ── Step 1: Bulk price/history download (one request for all tickers) ─────
    raw = None
    try:
        raw = yf.download(
            tickers, period="1y", group_by="ticker",
            progress=False, auto_adjust=True, threads=True,
            session=sess,
        )
    except Exception:
        pass

    price_snap: dict[str, dict] = {}
    if raw is not None and not raw.empty:
        has_multi = isinstance(raw.columns, pd.MultiIndex)
        for ticker in tickers:
            try:
                td = raw[ticker].dropna(subset=["Close"]) if has_multi else raw.dropna(subset=["Close"])
                if td.empty or len(td) < 2:
                    continue
                closes  = td["Close"]
                volumes = td["Volume"]
                price   = float(closes.iloc[-1])
                prev    = float(closes.iloc[-2])
                price_snap[ticker] = {
                    "price":      price,
                    "change":     price - prev,
                    "change_pct": (price - prev) / prev * 100 if prev else 0.0,
                    "volume":     int(volumes.iloc[-1]) if not volumes.empty else 0,
                    "avg_volume": int(volumes.mean()) if not volumes.empty else 0,
                    "ma_50":      float(closes.tail(50).mean()) if len(closes) >= 50 else None,
                    "ma_200":     float(closes.tail(200).mean()) if len(closes) >= 200 else None,
                    "wk52_hi":    float(closes.max()),
                    "wk52_lo":    float(closes.min()),
                    "change_1w":  float((closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100) if len(closes) >= 6 else 0.0,
                }
            except Exception:
                pass

    # ── Step 2: Sequential fundamentals fetch (1s gap avoids rate limits) ─────
    # Checks cache first — skips the sleep if already cached.
    fundamentals: dict[str, Optional[dict]] = {}
    done = 0
    for ticker in tickers:
        already_cached = bool(_get_cached(ticker, "yf_fundamentals", YF_FUNDAMENTALS_CACHE_HOURS * 60))
        _, fund = _fetch_yf_fundamentals(ticker, sess=sess)
        fundamentals[ticker] = fund
        if not already_cached:
            time.sleep(1.0)  # Only sleep when we actually hit Yahoo
        done += 1
        if progress_callback:
            progress_callback(done, total)

    # ── Step 3: Assemble StockQuotes ──────────────────────────────────────────
    for ticker in tickers:
        snap = price_snap.get(ticker)
        fund = fundamentals.get(ticker) or {}

        if not snap:
            results[ticker] = StockQuote(ticker=ticker, error="No price data from yfinance")
            continue

        price   = snap["price"]
        ma_50   = snap["ma_50"]
        ma_200  = snap["ma_200"]
        wk52_lo = snap["wk52_lo"]
        vol     = snap["volume"]
        avg_vol = snap["avg_volume"]
        vs_low  = ((price - wk52_lo) / wk52_lo * 100) if wk52_lo > 0 else 0.0
        div_yield = fund.get("div_yield")

        results[ticker] = StockQuote(
            ticker=ticker,
            name=fund.get("name") or ticker,
            price=price,
            change=snap["change"],
            change_pct=snap["change_pct"],
            volume=vol,
            avg_volume=avg_vol,
            volume_ratio=(vol / avg_vol) if avg_vol > 0 else 0.0,
            market_cap=fund.get("market_cap") or 0.0,
            pe_ratio=fund.get("pe_ratio"),
            pb_ratio=fund.get("pb_ratio"),
            eps=fund.get("eps"),
            dividend_yield=float(div_yield) if div_yield and div_yield > 0 else None,
            sector=fund.get("sector") or "",
            industry=fund.get("industry") or "",
            week_52_high=snap["wk52_hi"],
            week_52_low=wk52_lo,
            price_vs_52w_low_pct=vs_low,
            ma_50=ma_50,
            ma_200=ma_200,
            above_50ma=(price > ma_50) if ma_50 else False,
            above_200ma=(price > ma_200) if ma_200 else False,
            change_1w=snap["change_1w"],
            revenue_growth=fund.get("rev_growth"),
            earnings_growth=fund.get("earn_growth"),
            asset_type="stock",
        )

    return results


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
