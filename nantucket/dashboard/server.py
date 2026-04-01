"""
FastAPI web dashboard server.

Serves a dark-mode dashboard with:
- Overview: watchlist + portfolio snapshot + top movers
- Screener: interactive filter UI
- Portfolio: positions table + charts
- Stock Detail: price chart + AI analysis

To start: nantucket dashboard
URL: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from nantucket.db.database import init_db
from nantucket.watchlist import get_watchlist_quotes, get_watchlist, add_ticker, remove_ticker
from nantucket.portfolio import get_portfolio_summary, get_trade_history, record_buy, record_sell
from nantucket.screener import run_screen, ScreenFilters, load_presets
from nantucket.data.stocks import get_quote, get_history

app = FastAPI(title="NANTUCKET", description="Personal Investment Dashboard")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(_alert_check_loop())


async def _alert_check_loop():
    """Background task: check price alerts every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        try:
            from nantucket.alerts import check_alerts
            quotes_list = get_watchlist_quotes()
            quotes_dict = {q.ticker: q for q in quotes_list}
            check_alerts(quotes_dict)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Page routes
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    """Overview page: watchlist summary + portfolio snapshot."""
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/screener", response_class=HTMLResponse)
async def screener_page(request: Request):
    """Interactive screener page."""
    presets = load_presets()
    preset_names = list(presets.keys())
    return templates.TemplateResponse(
        request, "screener.html", {"preset_names": preset_names},
    )


@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request):
    """Portfolio positions and P&L chart."""
    return templates.TemplateResponse(request, "portfolio.html", {})


@app.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str):
    """Individual stock detail page."""
    return templates.TemplateResponse(
        request, "stock.html", {"ticker": ticker.upper()},
    )


@app.get("/market", response_class=HTMLResponse)
async def market_page(request: Request):
    """Market overview: movers, sectors, yield curve, economic calendar."""
    return templates.TemplateResponse(request, "market.html", {})


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Portfolio analytics: risk metrics, correlation matrix, tech summary."""
    return templates.TemplateResponse(request, "analytics.html", {})


# ──────────────────────────────────────────────────────────────────────────────
# JSON API routes (called by the frontend JavaScript)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/watchlist/tickers")
async def api_watchlist_tickers():
    """Return watchlist tickers instantly from DB — no price fetching."""
    entries = get_watchlist()
    return [{"ticker": e["ticker"], "asset_type": e["asset_type"]} for e in entries]


@app.get("/api/watchlist")
async def api_watchlist():
    """Return current watchlist with live quotes.
    Always returns all tickers; uses price=0 + error field if fetch fails."""
    entries = get_watchlist()
    if not entries:
        return []
    try:
        quotes = get_watchlist_quotes()
        fetched = {q.ticker: q for q in quotes}
    except Exception:
        fetched = {}

    # Always return every ticker, even if price failed
    result = []
    for e in entries:
        t = e["ticker"]
        if t in fetched:
            result.append(_quote_to_dict(fetched[t]))
        else:
            result.append({
                "ticker": t, "asset_type": e["asset_type"],
                "name": t, "price": 0, "change": 0, "change_pct": 0,
                "error": "Price unavailable",
            })
    return result


@app.get("/api/portfolio/raw")
async def api_portfolio_raw():
    """Return positions from DB instantly — no price fetching, uses avg_cost as price."""
    from nantucket.portfolio import get_positions_raw
    positions = get_positions_raw()
    return {
        "positions": [
            {
                "ticker": p["ticker"],
                "asset_type": p["asset_type"],
                "quantity": p["net_quantity"],
                "avg_cost": p["avg_cost"] or 0,
                "current_price": p["avg_cost"] or 0,
                "current_value": (p["net_quantity"] * (p["avg_cost"] or 0)),
                "cost_basis_total": (p["net_quantity"] * (p["avg_cost"] or 0)),
                "unrealized_pnl": 0,
                "unrealized_pnl_pct": 0,
                "allocation_pct": 0,
            }
            for p in positions
        ],
        "total_value": sum(p["net_quantity"] * (p["avg_cost"] or 0) for p in positions),
        "total_cost": sum(p["net_quantity"] * (p["avg_cost"] or 0) for p in positions),
        "total_pnl": 0,
        "total_pnl_pct": 0,
    }


@app.get("/api/portfolio")
async def api_portfolio():
    """Return portfolio positions with live P&L."""
    summary = get_portfolio_summary()
    return {
        "total_value": summary.total_value,
        "total_cost": summary.total_cost,
        "total_pnl": summary.total_pnl,
        "total_pnl_pct": summary.total_pnl_pct,
        "positions": [
            {
                "ticker": p.ticker,
                "asset_type": p.asset_type,
                "quantity": p.quantity,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "current_value": p.current_value,
                "cost_basis_total": p.cost_basis_total,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": p.unrealized_pnl_pct,
                "allocation_pct": p.allocation_pct,
            }
            for p in summary.positions
        ],
    }


@app.get("/api/screen")
async def api_screen(
    preset: Optional[str] = Query(None),
    pe_max: Optional[float] = Query(None),
    pb_max: Optional[float] = Query(None),
    eps_min: Optional[float] = Query(None),
    div_yield_min: Optional[float] = Query(None),
    volume_ratio_min: Optional[float] = Query(None),
    change_1w_min: Optional[float] = Query(None),
    above_50ma: Optional[bool] = Query(None),
    near_52w_low: Optional[float] = Query(None),
    sector: Optional[str] = Query(None),
    market_cap_min: Optional[float] = Query(None),
    limit: int = Query(15),
    universe: str = Query("top100"),
):
    """Run a screen and return JSON results."""
    # Start with preset if given
    if preset:
        from nantucket.screener import get_preset
        filters = get_preset(preset)
        if filters is None:
            raise HTTPException(status_code=404, detail=f"Preset '{preset}' not found")
    else:
        filters = ScreenFilters()

    # Override with any explicit query params
    if pe_max is not None:         filters.pe_max = pe_max
    if pb_max is not None:         filters.pb_max = pb_max
    if eps_min is not None:        filters.eps_min = eps_min
    if div_yield_min is not None:  filters.div_yield_min = div_yield_min
    if volume_ratio_min is not None: filters.volume_ratio_min = volume_ratio_min
    if change_1w_min is not None:  filters.change_1w_min = change_1w_min
    if above_50ma is not None:     filters.above_50ma = above_50ma
    if near_52w_low is not None:   filters.near_52w_low = near_52w_low
    if sector is not None:         filters.sector = sector
    if market_cap_min is not None: filters.market_cap_min = market_cap_min
    filters.limit = limit

    result = run_screen(filters, universe=universe, show_progress=False)
    return {
        "count": len(result.quotes),
        "universe_size": result.universe_size,
        "errors": result.errors,
        "results": [_quote_to_dict(q) for q in result.quotes],
    }


@app.get("/api/stock/{ticker}")
async def api_stock(ticker: str):
    """Return current quote + recent history for a stock."""
    ticker = ticker.upper()
    q = get_quote(ticker)
    hist = get_history(ticker, period="6mo")

    chart_data = {}
    if not hist.empty:
        chart_data = {
            "dates": hist.index.strftime("%Y-%m-%d").tolist(),
            "closes": [round(float(c), 2) for c in hist["Close"].tolist()],
            "volumes": [int(v) for v in hist["Volume"].tolist()],
        }

    return {
        "quote": _quote_to_dict(q),
        "chart": chart_data,
    }


@app.get("/api/stock/{ticker}/explain")
async def api_explain(ticker: str):
    """Generate AI explanation for a stock (requires ANTHROPIC_API_KEY)."""
    try:
        from nantucket.ai.analyst import explain_stock
        explanation = explain_stock(ticker.upper())
        return {"ticker": ticker.upper(), "explanation": explanation}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stock/{ticker}/bull-bear")
async def api_bull_bear(ticker: str):
    """Generate bull/bear analysis for a stock (requires ANTHROPIC_API_KEY)."""
    try:
        from nantucket.ai.analyst import bull_bear_case
        result = bull_bear_case(ticker.upper())
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Watchlist write endpoints
# ──────────────────────────────────────────────────────────────────────────────

class WatchlistAdd(BaseModel):
    ticker: str
    asset_type: str = "stock"

@app.post("/api/watchlist/add")
async def api_watchlist_add(body: WatchlistAdd):
    """Add a ticker to the watchlist."""
    ticker = body.ticker.upper().strip()
    # Auto-detect asset type
    asset_type = body.asset_type
    if asset_type == "stock":
        from nantucket.data.crypto import TICKER_TO_ID
        from nantucket.data.futures import FUTURES_UNIVERSE, ALIASES
        if ticker in TICKER_TO_ID:
            asset_type = "crypto"
        elif ticker in FUTURES_UNIVERSE or ticker in ALIASES:
            asset_type = "future"

    added = add_ticker(ticker, asset_type=asset_type)
    return {"success": added, "ticker": ticker, "asset_type": asset_type,
            "message": f"Added {ticker}" if added else f"{ticker} already in watchlist"}


@app.delete("/api/watchlist/{ticker}")
async def api_watchlist_remove(ticker: str):
    """Remove a ticker from the watchlist."""
    removed = remove_ticker(ticker.upper())
    return {"success": removed, "ticker": ticker.upper(),
            "message": f"Removed {ticker.upper()}" if removed else f"{ticker.upper()} not found"}


# ──────────────────────────────────────────────────────────────────────────────
# Trade write endpoints
# ──────────────────────────────────────────────────────────────────────────────

class TradeBody(BaseModel):
    ticker: str
    quantity: float
    price: Optional[float] = None
    asset_type: str = "stock"


@app.post("/api/trade/buy")
async def api_trade_buy(body: TradeBody):
    """Log a paper buy trade."""
    ticker = body.ticker.upper().strip()
    price = body.price
    if not price or price <= 0:
        raise HTTPException(status_code=400, detail="Price is required. Enter the price per share.")
    try:
        trade_id = record_buy(ticker, body.quantity, price, body.asset_type)
        return {"success": True, "trade_id": trade_id, "ticker": ticker,
                "action": "buy", "quantity": body.quantity, "price": price,
                "total": body.quantity * price}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/trade/sell")
async def api_trade_sell(body: TradeBody):
    """Log a paper sell trade."""
    ticker = body.ticker.upper().strip()
    price = body.price
    if not price or price <= 0:
        raise HTTPException(status_code=400, detail="Price is required. Enter the price per share.")
    try:
        trade_id = record_sell(ticker, body.quantity, price)
        return {"success": True, "trade_id": trade_id, "ticker": ticker,
                "action": "sell", "quantity": body.quantity, "price": price,
                "total": body.quantity * price}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/trades")
async def api_trades():
    """Return recent trade history."""
    trades = get_trade_history(limit=50)
    return [{"id": t.id, "ticker": t.ticker, "action": t.action,
             "quantity": t.quantity, "price": t.price,
             "total": t.quantity * t.price, "timestamp": t.timestamp,
             "asset_type": t.asset_type} for t in trades]


@app.get("/api/presets")
async def api_presets():
    """Return available screener presets."""
    presets = load_presets()
    return {
        name: {
            "description": data.get("description", ""),
            "learning_note": data.get("learning_note", ""),
        }
        for name, data in presets.items()
    }


# ──────────────────────────────────────────────────────────────────────────────
# Bloomberg-style: news, movers, sectors, yield curve, earnings, econ calendar
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/news")
async def api_news(
    ticker: Optional[str] = Query(None),
    limit: int = Query(15),
):
    """Return market news headlines filtered to watchlist or a specific ticker."""
    from nantucket.data.news import get_news_for_tickers, get_market_news
    if ticker:
        feed = get_news_for_tickers([ticker.upper()], limit=limit)
    else:
        watchlist = get_watchlist()
        tickers = [e["ticker"] for e in watchlist]
        feed = get_news_for_tickers(tickers, limit=limit) if tickers else get_market_news(limit=limit)
    return {
        "items": [i.__dict__ for i in feed.items],
        "fetched_at": feed.fetched_at,
        "error": feed.error,
    }


@app.get("/api/movers")
async def api_movers(limit: int = Query(10)):
    """Return top S&P 100 gainers, losers, and most active."""
    from nantucket.data.stocks import get_quotes_batch, TOP_100_SP500
    quotes_dict = get_quotes_batch(list(TOP_100_SP500), max_workers=8)
    all_q = [q for q in quotes_dict.values() if not q.error and q.price > 0]

    def _to_row(q):
        return {
            "ticker": q.ticker, "name": q.name,
            "price": round(q.price, 2),
            "change": round(q.change, 2),
            "change_pct": round(q.change_pct or 0, 2),
            "volume": q.volume,
            "volume_ratio": round(q.volume_ratio or 0, 2),
            "market_cap": q.market_cap,
            "sector": q.sector,
        }

    gainers = [_to_row(q) for q in sorted(all_q, key=lambda q: q.change_pct or 0, reverse=True)[:limit]]
    losers  = [_to_row(q) for q in sorted(all_q, key=lambda q: q.change_pct or 0)[:limit]]
    active  = [_to_row(q) for q in sorted(all_q, key=lambda q: q.volume_ratio or 0, reverse=True)[:limit]]
    return {"gainers": gainers, "losers": losers, "active": active}


@app.get("/api/sectors")
async def api_sectors():
    """Return S&P sector ETF performance heatmap."""
    from nantucket.data.sectors import get_sector_heatmap
    heatmap = get_sector_heatmap()
    return {
        "sectors": [s.__dict__ for s in heatmap.sectors],
        "fetched_at": heatmap.fetched_at,
        "error": heatmap.error,
    }


@app.get("/api/yield-curve")
async def api_yield_curve():
    """Return current US Treasury yield curve."""
    from nantucket.data.treasury import get_yield_curve
    curve = get_yield_curve()
    return {
        "points": [p.__dict__ for p in curve.points],
        "is_inverted": curve.is_inverted,
        "spread_10y3m": curve.spread_10y3m,
        "fetched_at": curve.fetched_at,
        "error": curve.error,
    }


@app.get("/api/earnings")
async def api_earnings(days: int = Query(14)):
    """Return upcoming earnings dates for watchlist tickers."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date, timedelta
    import yfinance as yf

    watchlist = get_watchlist()
    stock_tickers = [e["ticker"] for e in watchlist if e["asset_type"] in ("stock", "etf")]
    today = date.today()
    cutoff = today + timedelta(days=days)
    results = []

    def _check(ticker: str):
        try:
            cal = yf.Ticker(ticker).calendar
            if not isinstance(cal, dict):
                return
            for ed in cal.get("Earnings Date", []):
                if ed is None:
                    continue
                try:
                    ed_date = ed.date() if hasattr(ed, "date") else date.fromisoformat(str(ed)[:10])
                    if today <= ed_date <= cutoff:
                        results.append({
                            "ticker": ticker,
                            "date": str(ed_date),
                            "days_away": (ed_date - today).days,
                        })
                except Exception:
                    pass
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_check, stock_tickers))

    results.sort(key=lambda r: r["date"])
    return {"earnings": results, "days": days}


@app.get("/api/econ-calendar")
async def api_econ_calendar():
    """Return upcoming economic calendar events from FRED."""
    from nantucket.data.econ import get_econ_calendar
    cal = get_econ_calendar()
    return {
        "events": [e.__dict__ for e in cal.events],
        "fetched_at": cal.fetched_at,
        "error": cal.error,
    }


@app.get("/api/analytics")
async def api_analytics(period: str = Query("1y")):
    """Return portfolio risk/return analytics."""
    from nantucket.analytics import get_portfolio_analytics
    summary = get_portfolio_summary()
    result = get_portfolio_analytics(summary.positions, period=period)
    return result.__dict__


@app.get("/api/correlation")
async def api_correlation(
    tickers: str = Query(..., description="Comma-separated tickers"),
    period: str = Query("1y"),
):
    """Return pairwise correlation matrix for the given tickers."""
    from nantucket.analytics import get_correlation_matrix
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    result = get_correlation_matrix(ticker_list, period=period)
    return result.__dict__


@app.get("/api/stock/{ticker}/indicators")
async def api_indicators(ticker: str, period: str = Query("1y")):
    """Return RSI, MACD, and Bollinger Band data for a stock."""
    from nantucket.data.indicators import get_indicators
    result = get_indicators(ticker.upper(), period=period)
    return {
        "ticker": result.ticker,
        "rsi": result.rsi.__dict__ if result.rsi else None,
        "macd": result.macd.__dict__ if result.macd else None,
        "bollinger": result.bollinger.__dict__ if result.bollinger else None,
        "error": result.error,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Alerts CRUD
# ──────────────────────────────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    ticker: str
    direction: str   # "above" | "below"
    target: float
    note: str = ""


@app.get("/api/alerts")
async def api_alerts_get(ticker: Optional[str] = Query(None)):
    """Return active alerts, optionally filtered by ticker."""
    from nantucket.alerts import get_alerts, get_recent_triggers
    alerts = get_alerts(ticker=ticker, active_only=False)
    triggers = get_recent_triggers(limit=5)
    return {
        "alerts": [a.__dict__ for a in alerts if a.active],
        "recent_triggers": [a.__dict__ for a in triggers],
    }


@app.post("/api/alerts")
async def api_alerts_create(body: AlertCreate):
    """Create a new price alert."""
    from nantucket.alerts import add_alert
    try:
        alert = add_alert(body.ticker.upper(), body.direction, body.target, body.note)
        return {"success": True, "alert": alert.__dict__}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/alerts/{alert_id}")
async def api_alerts_delete(alert_id: int):
    """Delete an alert by ID."""
    from nantucket.alerts import remove_alert
    removed = remove_alert(alert_id)
    return {"success": removed}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _quote_to_dict(q) -> dict:
    """Convert a StockQuote to a JSON-serializable dict."""
    return {
        "ticker": q.ticker,
        "name": q.name,
        "price": round(q.price, 2),
        "change": round(q.change, 2),
        "change_pct": round(q.change_pct, 2),
        "volume": q.volume,
        "avg_volume": q.avg_volume,
        "volume_ratio": round(q.volume_ratio, 2),
        "market_cap": q.market_cap,
        "pe_ratio": round(q.pe_ratio, 1) if q.pe_ratio else None,
        "pb_ratio": round(q.pb_ratio, 2) if q.pb_ratio else None,
        "eps": round(q.eps, 2) if q.eps else None,
        "dividend_yield": round(q.dividend_yield, 2) if q.dividend_yield else None,
        "sector": q.sector,
        "industry": q.industry,
        "week_52_high": round(q.week_52_high, 2),
        "week_52_low": round(q.week_52_low, 2),
        "change_1w": round(q.change_1w, 2),
        "above_50ma": q.above_50ma,
        "above_200ma": q.above_200ma,
        "revenue_growth": round(q.revenue_growth * 100, 1) if q.revenue_growth else None,
        "asset_type": q.asset_type,
        "error": q.error,
    }
