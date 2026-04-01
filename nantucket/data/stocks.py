"""
Stock data via Yahoo Finance chart API — free, no API key, no yfinance dependency.

Price fetching calls Yahoo Finance's v8/finance/chart endpoint directly via
requests. This bypasses yfinance's curl_cffi backend which fails on some
macOS configurations.

Fundamentals (P/E, sector, etc.) still use yfinance .info since there is
no simple public alternative; they are cached 24h to minimise calls.

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

from nantucket.data._session import get_session


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

QUOTE_CACHE_MINUTES   = 15
OVERVIEW_CACHE_HOURS  = 24
HISTORY_CACHE_MINUTES = 60

_YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_YF_CHART2 = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"  # fallback


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
# Yahoo Finance chart API (direct, no yfinance)
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        f = float(val)
        return None if f != f else f  # NaN → None
    except (TypeError, ValueError):
        return None


def _fetch_chart(ticker: str, range_: str = "7d", interval: str = "1d") -> Optional[dict]:
    """Call Yahoo Finance chart API directly. Returns the raw 'chart.result[0]' dict."""
    params = {"interval": interval, "range": range_}
    for url_tpl in (_YF_CHART, _YF_CHART2):
        try:
            url = url_tpl.format(ticker=ticker)
            resp = get_session().get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("chart", {}).get("result") or []
                if results:
                    return results[0]
        except Exception:
            continue
    return None


def _fetch_quote_from_chart(ticker: str) -> Optional[dict]:
    """Fetch price/change/volume from Yahoo chart API, with 15-min cache."""
    cached = _get_cached(ticker, "yf_quote", QUOTE_CACHE_MINUTES)
    if cached:
        return cached

    result = _fetch_chart(ticker, range_="7d", interval="1d")
    if not result:
        return None

    try:
        meta   = result.get("meta", {})
        price  = _safe_float(meta.get("regularMarketPrice")) or 0.0
        prev   = _safe_float(meta.get("chartPreviousClose") or meta.get("previousClose")) or price
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0.0
        volume = int(meta.get("regularMarketVolume") or 0)

        # 1-week change from timestamps array
        closes = (result.get("indicators", {})
                       .get("quote", [{}])[0]
                       .get("close") or [])
        closes = [c for c in closes if c is not None]
        change_1w = 0.0
        if len(closes) >= 6:
            p6 = closes[-6]
            if p6:
                change_1w = ((price - p6) / p6 * 100)

        q_data = {
            "price": price, "change": change,
            "change_pct": change_pct, "volume": volume,
            "change_1w": change_1w,
        }
        if price > 0:
            _set_cache(ticker, "yf_quote", q_data)
        return q_data
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Fundamentals via yfinance .info (cached 24h)
# ──────────────────────────────────────────────────────────────────────────────

_YF_SUMMARY = "https://query1.finance.yahoo.com/v11/finance/quoteSummary/{ticker}"
_YF_SUMMARY2 = "https://query2.finance.yahoo.com/v11/finance/quoteSummary/{ticker}"
_YF_MODULES = "defaultKeyStatistics,summaryDetail,assetProfile,financialData"


def _fetch_info(ticker: str) -> dict:
    """Fetch P/E, sector, market cap etc. via Yahoo quoteSummary API, cached 24h."""
    cached = _get_cached(ticker, "yf_info", OVERVIEW_CACHE_HOURS * 60)
    if cached:
        return cached
    try:
        params = {"modules": _YF_MODULES, "formatted": "false"}
        data = None
        for url_tpl in (_YF_SUMMARY, _YF_SUMMARY2):
            try:
                resp = get_session().get(
                    url_tpl.format(ticker=ticker), params=params, timeout=12
                )
                if resp.status_code == 200:
                    data = resp.json().get("quoteSummary", {}).get("result") or []
                    if data:
                        data = data[0]
                        break
            except Exception:
                continue

        if not data:
            return {}

        ks  = data.get("defaultKeyStatistics", {})
        sd  = data.get("summaryDetail", {})
        ap  = data.get("assetProfile", {})
        fd  = data.get("financialData", {})

        def _v(d, key):
            val = d.get(key)
            if isinstance(val, dict):
                val = val.get("raw")
            return _safe_float(val)

        result = {
            "name":            ap.get("longBusinessSummary", ticker)[:40] if ap.get("longBusinessSummary") else ticker,
            "sector":          ap.get("sector") or "",
            "industry":        ap.get("industry") or "",
            "market_cap":      _v(sd, "marketCap") or 0.0,
            "pe_ratio":        _v(sd, "trailingPE"),
            "pb_ratio":        _v(ks, "priceToBook"),
            "eps":             _v(ks, "trailingEps"),
            "dividend_yield":  (_v(sd, "dividendYield") or 0.0) * 100,
            "week_52_high":    _v(sd, "fiftyTwoWeekHigh") or 0.0,
            "week_52_low":     _v(sd, "fiftyTwoWeekLow") or 0.0,
            "ma_50":           _v(sd, "fiftyDayAverage"),
            "ma_200":          _v(sd, "twoHundredDayAverage"),
            "avg_volume":      int(_v(sd, "averageVolume") or 0),
            "revenue_growth":  _v(fd, "revenueGrowth"),
            "earnings_growth": _v(fd, "earningsGrowth"),
        }
        if result["sector"] or result["pe_ratio"]:
            _set_cache(ticker, "yf_info", result)
        return result
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# Quote builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_quote(ticker: str, q: dict, info: dict) -> StockQuote:
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
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(ticker: str) -> StockQuote:
    """Full quote for a single ticker."""
    ticker = ticker.upper().strip()
    try:
        q_data = _fetch_quote_from_chart(ticker)
        if not q_data or q_data["price"] == 0:
            return StockQuote(ticker=ticker, error=f"No price data for {ticker}")
        info = _fetch_info(ticker)
        return _build_quote(ticker, q_data, info)
    except Exception as e:
        return StockQuote(ticker=ticker, error=str(e))


def get_quotes_batch(
    tickers: list[str],
    max_workers: int = 10,
    progress_callback=None,
    with_fundamentals: bool = False,
) -> dict[str, StockQuote]:
    """
    Fetch quotes for many tickers in parallel via Yahoo chart API.
    with_fundamentals=True also fetches P/E, P/B, sector (for screener).
    """
    tickers = [t.upper() for t in tickers]
    if not tickers:
        return {}

    results: dict[str, StockQuote] = {}
    stale: list[str] = []

    for t in tickers:
        cached_q = _get_cached(t, "yf_quote", QUOTE_CACHE_MINUTES)
        if cached_q:
            cached_i = _get_cached(t, "yf_info", OVERVIEW_CACHE_HOURS * 60) or {}
            results[t] = _build_quote(t, cached_q, cached_i)
        else:
            stale.append(t)

    if stale:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_quote_from_chart, t): t for t in stale}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    q_data = future.result()
                    if q_data and q_data["price"] > 0:
                        info = _get_cached(t, "yf_info", OVERVIEW_CACHE_HOURS * 60) or {}
                        results[t] = _build_quote(t, q_data, info)
                    else:
                        results[t] = StockQuote(ticker=t, error="No price data")
                except Exception as exc:
                    results[t] = StockQuote(ticker=t, error=str(exc))
                if progress_callback:
                    progress_callback(len(results), len(tickers))

    if with_fundamentals:
        needs_info = [t for t in tickers
                      if t in results and not results[t].error and not results[t].sector]
        if needs_info:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_fetch_info, t): t for t in needs_info}
                for future in as_completed(futures):
                    t = futures[future]
                    try:
                        info = future.result()
                        q = results.get(t)
                        if q and info and not q.error:
                            q_data = {
                                "price": q.price, "change": q.change,
                                "change_pct": q.change_pct, "volume": q.volume,
                                "change_1w": q.change_1w,
                            }
                            results[t] = _build_quote(t, q_data, info)
                    except Exception:
                        pass

    return results


def get_history(
    ticker: str,
    period: str = "1y",
    outputsize: str = "full",
    interval: str = "1d",
) -> pd.DataFrame:
    """OHLCV price history via Yahoo chart API, cached 60 minutes."""
    cache_key = f"yf_hist_{period}_{interval}"
    cached = _get_cached(ticker, cache_key, HISTORY_CACHE_MINUTES)
    if cached:
        try:
            df = pd.DataFrame(cached["data"])
            df.index = pd.to_datetime(df.index)
            return df.sort_index()
        except Exception:
            pass

    # Map period string to Yahoo Finance range parameter
    range_map = {
        "1d": "1d", "5d": "5d", "1mo": "1mo", "3mo": "3mo",
        "6mo": "6mo", "1y": "1y", "2y": "2y", "5y": "5y", "10d": "5d",
    }
    yf_range = range_map.get(period, "1y")

    result = _fetch_chart(ticker, range_=yf_range, interval=interval)
    if not result:
        return pd.DataFrame()

    try:
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {}).get("quote", [{}])[0]
        opens   = indicators.get("open", [])
        highs   = indicators.get("high", [])
        lows    = indicators.get("low", [])
        closes  = indicators.get("close", [])
        volumes = indicators.get("volume", [])

        rows = []
        for i, ts in enumerate(timestamps):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            rows.append({
                "Date":   pd.Timestamp(ts, unit="s"),
                "Open":   opens[i]   if i < len(opens)   else c,
                "High":   highs[i]   if i < len(highs)   else c,
                "Low":    lows[i]    if i < len(lows)    else c,
                "Close":  c,
                "Volume": volumes[i] if i < len(volumes) else 0,
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("Date")
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
