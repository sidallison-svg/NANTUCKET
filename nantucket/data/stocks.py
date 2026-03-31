"""
Stock data via yfinance — free, no API key required, fast batch fetching.

Alpha Vantage is kept as optional fallback for screener fundamentals only.

Cache strategy (SQLite):
- Quotes:      15 minutes  (price / change / volume)
- Fundamentals: 24 hours   (P/E, sector, market cap — rarely changes)
- History:      60 minutes  (OHLCV daily bars)
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
# yfinance helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        f = float(val)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def _fetch_info(ticker: str) -> dict:
    """Fetch fundamentals from yfinance .info, with 24h cache."""
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


def _fetch_fast_quote(ticker: str) -> Optional[dict]:
    """Fetch current price/change/volume/1w-change, with 15-min cache."""
    cached = _get_cached(ticker, "yf_quote", QUOTE_CACHE_MINUTES)
    if cached:
        return cached

    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info
        price      = _safe_float(fi.last_price) or 0.0
        prev_close = _safe_float(fi.previous_close) or price
        change     = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        volume     = int(_safe_float(fi.last_volume) or 0)
        change_1w  = _get_weekly_change(ticker)

        result = {
            "price":      price,
            "change":     change,
            "change_pct": change_pct,
            "volume":     volume,
            "change_1w":  change_1w,
        }
        if price > 0:
            _set_cache(ticker, "yf_quote", result)
        return result
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Public API — same interface as before
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(ticker: str) -> StockQuote:
    ticker = ticker.upper().strip()
    try:
        q = _fetch_fast_quote(ticker)
        if not q or q["price"] == 0:
            return StockQuote(ticker=ticker, error=f"No data for {ticker} — check the symbol")

        ov = _fetch_info(ticker)
        price      = q["price"]
        avg_volume = ov.get("avg_volume") or 0
        w52_low    = ov.get("week_52_low") or 0.0
        vs_low     = ((price - w52_low) / w52_low * 100) if w52_low > 0 else 0.0
        ma50       = ov.get("ma_50")
        ma200      = ov.get("ma_200")
        div_yield  = ov.get("dividend_yield") or 0.0

        change_1w = _get_weekly_change(ticker)

        return StockQuote(
            ticker=ticker,
            name=ov.get("name") or ticker,
            price=price,
            change=q["change"],
            change_pct=q["change_pct"],
            volume=q["volume"],
            avg_volume=avg_volume,
            volume_ratio=(q["volume"] / avg_volume) if avg_volume > 0 else 0.0,
            market_cap=ov.get("market_cap") or 0.0,
            pe_ratio=ov.get("pe_ratio"),
            pb_ratio=ov.get("pb_ratio"),
            eps=ov.get("eps"),
            dividend_yield=div_yield if div_yield > 0 else None,
            sector=ov.get("sector") or "",
            industry=ov.get("industry") or "",
            week_52_high=ov.get("week_52_high") or 0.0,
            week_52_low=w52_low,
            price_vs_52w_low_pct=vs_low,
            ma_50=ma50,
            ma_200=ma200,
            above_50ma=(price > ma50) if ma50 else False,
            above_200ma=(price > ma200) if ma200 else False,
            change_1w=change_1w,
            revenue_growth=ov.get("revenue_growth"),
            earnings_growth=ov.get("earnings_growth"),
            asset_type="stock",
        )

    except Exception as e:
        return StockQuote(ticker=ticker, error=str(e))


def _get_weekly_change(ticker: str) -> float:
    try:
        hist = get_history(ticker, period="10d")
        if not hist.empty and len(hist) >= 6:
            start = hist["Close"].iloc[-6]
            end   = hist["Close"].iloc[-1]
            return ((end - start) / start * 100) if start else 0.0
    except Exception:
        pass
    return 0.0


def get_quotes_batch(
    tickers: list[str],
    max_workers: int = 10,
    progress_callback=None,
) -> dict[str, StockQuote]:
    """
    Fetch quotes for many tickers in parallel using yfinance.
    No rate limits, no artificial delays.
    """
    tickers = [t.upper() for t in tickers]
    results: dict[str, StockQuote] = {}

    # Check cache first — only fetch stale ones
    stale = []
    for t in tickers:
        cached_q = _get_cached(t, "yf_quote", QUOTE_CACHE_MINUTES)
        cached_i = _get_cached(t, "yf_info", OVERVIEW_CACHE_HOURS * 60)
        if cached_q and cached_i:
            price     = cached_q["price"]
            avg_vol   = cached_i.get("avg_volume") or 0
            w52_low   = cached_i.get("week_52_low") or 0.0
            vs_low    = ((price - w52_low) / w52_low * 100) if w52_low > 0 else 0.0
            ma50      = cached_i.get("ma_50")
            ma200     = cached_i.get("ma_200")
            div_yield = cached_i.get("dividend_yield") or 0.0
            results[t] = StockQuote(
                ticker=t,
                name=cached_i.get("name") or t,
                price=price,
                change=cached_q["change"],
                change_pct=cached_q["change_pct"],
                volume=cached_q["volume"],
                avg_volume=avg_vol,
                volume_ratio=(cached_q["volume"] / avg_vol) if avg_vol > 0 else 0.0,
                market_cap=cached_i.get("market_cap") or 0.0,
                pe_ratio=cached_i.get("pe_ratio"),
                pb_ratio=cached_i.get("pb_ratio"),
                eps=cached_i.get("eps"),
                dividend_yield=div_yield if div_yield > 0 else None,
                sector=cached_i.get("sector") or "",
                industry=cached_i.get("industry") or "",
                week_52_high=cached_i.get("week_52_high") or 0.0,
                week_52_low=w52_low,
                price_vs_52w_low_pct=vs_low,
                ma_50=ma50,
                ma_200=ma200,
                above_50ma=(price > ma50) if ma50 else False,
                above_200ma=(price > ma200) if ma200 else False,
                asset_type="stock",
            )
        else:
            stale.append(t)

    done = len(results)
    if stale:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_ticker = {pool.submit(get_quote, t): t for t in stale}
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


def get_history(
    ticker: str,
    period: str = "1y",
    outputsize: str = "full",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Get OHLCV price history via yfinance. Cached for 60 minutes.
    """
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
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
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
