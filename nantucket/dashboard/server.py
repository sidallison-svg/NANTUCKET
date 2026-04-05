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


# ──────────────────────────────────────────────────────────────────────────────
# JSON API routes (called by the frontend JavaScript)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/watchlist")
async def api_watchlist():
    """Return current watchlist with live quotes."""
    quotes = get_watchlist_quotes()
    return [_quote_to_dict(q) for q in quotes]


@app.get("/api/portfolio")
async def api_portfolio():
    """Return portfolio positions with P&L."""
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


def _detect_asset_type(ticker: str, hint: str = "stock") -> str:
    """Auto-detect asset type from ticker, falling back to hint."""
    if hint != "stock":
        return hint
    from nantucket.data.crypto import TICKER_TO_ID
    from nantucket.data.futures import FUTURES_UNIVERSE, ALIASES
    if ticker in TICKER_TO_ID:
        return "crypto"
    if ticker in FUTURES_UNIVERSE or ticker in ALIASES:
        return "future"
    return "stock"


def _fetch_price_for_trade(ticker: str, asset_type: str) -> float:
    """Fetch current market price, using the right data source for the asset type."""
    if asset_type == "crypto":
        from nantucket.data.crypto import get_crypto_quote
        cq = get_crypto_quote(ticker)
        if cq.error or cq.price == 0:
            raise HTTPException(status_code=400, detail=f"Could not fetch price for {ticker}")
        return cq.price
    else:
        q = get_quote(ticker)
        if q.error or q.price == 0:
            raise HTTPException(status_code=400, detail=f"Could not fetch price for {ticker}")
        return q.price


@app.post("/api/trade/buy")
async def api_trade_buy(body: TradeBody):
    """Log a paper buy trade."""
    ticker = body.ticker.upper().strip()
    asset_type = _detect_asset_type(ticker, body.asset_type)
    price = body.price if body.price else _fetch_price_for_trade(ticker, asset_type)
    try:
        trade_id = record_buy(ticker, body.quantity, price, asset_type)
        return {"success": True, "trade_id": trade_id, "ticker": ticker,
                "action": "buy", "quantity": body.quantity, "price": price,
                "total": body.quantity * price}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/trade/sell")
async def api_trade_sell(body: TradeBody):
    """Log a paper sell trade."""
    ticker = body.ticker.upper().strip()
    asset_type = _detect_asset_type(ticker, body.asset_type)
    price = body.price if body.price else _fetch_price_for_trade(ticker, asset_type)
    try:
        trade_id = record_sell(ticker, body.quantity, price, asset_type)
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
