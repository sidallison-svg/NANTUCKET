"""
Stock data via yfinance — free, no API key required, fast batch fetching.

For watchlist/portfolio: uses yf.download() — one request for all tickers.
For stock detail page: uses yf.Ticker().info for full fundamentals.

Cache strategy (SQLite):
- Quotes:       15 minutes  (price / change / volume)
- Fundamentals: 24 hours    (P/E, sector, market cap)
- History:      60 minutes  (OHLCV daily bars)
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

QUOTE_CACHE_MINUTES   = 15
OVERVIEW_CACHE_HOURS  = 24
HISTORY_CACHE_MINUTES = 60


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class StockQuote:
    ticker: str
    name: str = ""
    price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    avg_volume: int = 0
    volume_ratio: float = 0.0
    market_cap: float = 0.0
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
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
            "SELECT data_json, fetched_at FROM quote_cache WHERE ticker=? AND data_type=?",
            (ticker.upper(), data_type),
        ).fetchone()
        if not row:
            return None
        age = datetime.now() - datetime.fromisoformat(row["fetched_at"])
        if age < timedelta(minutes=max_age_minutes):
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
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        f = float(val)
        return None if f != f else f  # NaN → None
    except (TypeError, ValueError):
        return None


def _build_quote(ticker: str, q: dict, info: dict) -> StockQuote:
    """Assemble a StockQuote from price data dict + info dict."""
    price   = q.get("price", 0.0)
    avg_vol = info.get("avg_volume") or 0
    w52_low = info.get("week_52_low") or 0.0
    vs_low  = ((price - w52_low) / w52_low * 100) if w52_low > 0 else 0.0
    ma50    = info.get("ma_50")
    ma200   = info.get("ma_200")
    div     = info.get("dividend_yield") or 0.0
    vol     = q.get("volume", 0)

    return StockQuote(
        ticker=ticker,
        name=info.get("name") or ticker,
        price=price,
        change=q.get("change", 0.0),
        change_pct=q.get("change_pct", 0.0),
        volume=vol,
        avg_volume=avg_vol,
        volume_ratio=(vol / avg_vol) if avg_vol > 0 else 0.0,
        market_cap=info.get("market_cap") or 0.0,
        pe_ratio=info.get("pe_ratio"),
        pb_ratio=info.get("pb_ratio"),
        eps=info.get("eps"),
        dividend_yield=div if div > 0 else None,
        sector=info.get("sector") or "",
        industry=info.get("industry") or "",
        week_52_high=info.get("week_52_high") or 0.0,
        week_52_low=w52_low,
        price_vs_52w_low_pct=vs_low,
        ma_50=ma50,
        ma_200=ma200,
        above_50ma=(price > ma50) if ma50 else False,
        above_200ma=(price > ma200) if ma200 else False,
        change_1w=q.get("change_1w", 0.0),
        revenue_growth=info.get("revenue_growth"),
        earnings_growth=info.get("earnings_growth"),
        asset_type="stock",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Batch quote — uses yf.download() for speed (1 request for all tickers)
# ──────────────────────────────────────────────────────────────────────────────

def get_quotes_batch(
    tickers: list[str],
    max_workers: int = 10,
    progress_callback=None,
) -> dict[str, StockQuote]:
    """
    Fetch quotes for many tickers in a single yfinance download request.
    Falls back to individual fetches if batch fails.
    """
    tickers = [t.upper() for t in tickers]
    if not tickers:
        return {}

    results: dict[str, StockQuote] = {}
    stale: list[str] = []

    # Serve from cache where possible
    for t in tickers:
        cached_q = _get_cached(t, "yf_quote", QUOTE_CACHE_MINUTES)
        if cached_q:
            cached_i = _get_cached(t, "yf_info", OVERVIEW_CACHE_HOURS * 60) or {}
            results[t] = _build_quote(t, cached_q, cached_i)
        else:
            stale.append(t)

    if not stale:
        if progress_callback:
            progress_callback(len(tickers), len(tickers))
        return results

    # Batch download: ONE HTTP request for all stale tickers
    try:
        raw = yf.download(
            stale,
            period="7d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )

        for ticker in stale:
            try:
                # yf.download returns MultiIndex when multiple tickers
                if len(stale) == 1:
                    close  = raw["Close"].dropna()
                    volume = raw["Volume"].dropna()
                else:
                    close  = raw["Close"][ticker].dropna()
                    volume = raw["Volume"][ticker].dropna()

                if close.empty or len(close) < 2:
                    results[ticker] = StockQuote(ticker=ticker, error="No price data")
                    continue

                price      = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                change     = price - prev_close
                change_pct = (change / prev_close * 100) if prev_close else 0.0
                vol        = int(volume.iloc[-1]) if not volume.empty else 0
                change_1w  = 0.0
                if len(close) >= 6:
                    p6 = float(close.iloc[-6])
                    change_1w = ((price - p6) / p6 * 100) if p6 else 0.0

                q_data = {
                    "price": price, "change": change,
                    "change_pct": change_pct, "volume": vol,
                    "change_1w": change_1w,
                }
                _set_cache(ticker, "yf_quote", q_data)

                info = _get_cached(ticker, "yf_info", OVERVIEW_CACHE_HOURS * 60) or {}
                results[ticker] = _build_quote(ticker, q_data, info)

            except Exception as exc:
                results[ticker] = StockQuote(ticker=ticker, error=str(exc))

            if progress_callback:
                progress_callback(len(results), len(tickers))

    except Exception:
        # Fallback: individual fetches in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(get_quote, t): t for t in stale}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    results[t] = future.result()
                except Exception as exc:
                    results[t] = StockQuote(ticker=t, error=str(exc))
                if progress_callback:
                    progress_callback(len(results), len(tickers))

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Single quote — full detail for stock page
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_info(ticker: str) -> dict:
    """Fetch fundamentals from yfinance .info, cached 24h."""
    cached = _get_cached(ticker, "yf_info", OVERVIEW_CACHE_HOURS * 60)
    if cached:
        return cached
    try:
        info = yf.Ticker(ticker).info or {}
        result = {
            "name":            info.get("longName") or info.get("shortName") or ticker,
            "sector":          info.get("sector") or "",
            "industry":        info.get("industry") or "",
            "market_cap":      _safe_float(info.get("marketCap")) or 0.0,
            "pe_ratio":        _safe_float(info.get("trailingPE")),
            "pb_ratio":        _safe_float(info.get("priceToBook")),
            "eps":             _safe_float(info.get("trailingEps")),
            "dividend_yield":  (_safe_float(info.get("dividendYield")) or 0.0) * 100,
            "week_52_high":    _safe_float(info.get("fiftyTwoWeekHigh")) or 0.0,
            "week_52_low":     _safe_float(info.get("fiftyTwoWeekLow")) or 0.0,
            "ma_50":           _safe_float(info.get("fiftyDayAverage")),
            "ma_200":          _safe_float(info.get("twoHundredDayAverage")),
            "avg_volume":      int(_safe_float(info.get("averageVolume")) or 0),
            "revenue_growth":  _safe_float(info.get("revenueGrowth")),
            "earnings_growth": _safe_float(info.get("earningsGrowth")),
        }
        _set_cache(ticker, "yf_info", result)
        return result
    except Exception:
        return {}


def get_quote(ticker: str) -> StockQuote:
    """Full quote for a single ticker (used on stock detail page)."""
    ticker = ticker.upper().strip()
    try:
        # Use batch download for a single ticker to keep one code path
        batch = get_quotes_batch([ticker])
        q = batch.get(ticker)
        if q is None:
            return StockQuote(ticker=ticker, error="No data")

        # Enrich with full fundamentals if not already cached
        if not q.sector and not q.pe_ratio:
            info = _fetch_info(ticker)
            q = _build_quote(ticker,
                {"price": q.price, "change": q.change, "change_pct": q.change_pct,
                 "volume": q.volume, "change_1w": q.change_1w},
                info)
        return q
    except Exception as e:
        return StockQuote(ticker=ticker, error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Historical data
# ──────────────────────────────────────────────────────────────────────────────

def get_history(
    ticker: str,
    period: str = "1y",
    outputsize: str = "full",
    interval: str = "1d",
) -> pd.DataFrame:
    """OHLCV price history via yfinance, cached 60 minutes."""
    cache_key = f"yf_hist_{period}_{interval}"
    cached = _get_cached(ticker, cache_key, HISTORY_CACHE_MINUTES)
    if cached:
        try:
            df = pd.DataFrame(cached["data"])
            df.index = pd.to_datetime(df.index)
            return df.sort_index()
        except Exception:
            pass

    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        _set_cache(ticker, cache_key, {"data": df.to_dict()})
        return df
    except Exception:
        return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────────────
# Screener universe
# ──────────────────────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        return [t for t in tickers if isinstance(t, str)]
    except Exception:
        return list(TOP_100_SP500)


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
