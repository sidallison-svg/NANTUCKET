"""
Market intelligence — daily pulse, sector heatmap, economic indicators, and AI analysis.

Data sources:
- yfinance: index prices, sector ETFs, VIX (free, no key)
- FRED API: CPI, unemployment, GDP, Fed Funds Rate, etc. (free key at fred.stlouisfed.org)
- Anthropic Claude: AI-generated market narrative and event analysis

Set FRED_API_KEY env var for live economic data.
Without it, indicators still show with educational content but no live values.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

import yfinance as yf

# ─────────────────────────────────────────────────────────
# Market indices and sector ETFs
# ─────────────────────────────────────────────────────────

INDICES = {
    "S&P 500":    "^GSPC",
    "NASDAQ":     "^IXIC",
    "DOW":        "^DJI",
    "Russell 2000": "^RUT",
}

SECTOR_ETFS = {
    "Technology":       "XLK",
    "Healthcare":       "XLV",
    "Financials":       "XLF",
    "Energy":           "XLE",
    "Consumer Disc.":   "XLY",
    "Consumer Staples": "XLP",
    "Industrials":      "XLI",
    "Materials":        "XLB",
    "Real Estate":      "XLRE",
    "Utilities":        "XLU",
    "Communication":    "XLC",
}


def get_market_pulse() -> dict:
    """Fetch current index prices and daily % changes."""
    tickers = list(INDICES.values())
    try:
        data = yf.download(tickers, period="2d", group_by="ticker", progress=False, auto_adjust=True)
    except Exception:
        return {}

    results = {}
    for name, symbol in INDICES.items():
        try:
            closes = data[symbol]["Close"].dropna()
            if len(closes) < 2:
                continue
            close_today = float(closes.iloc[-1])
            close_prev = float(closes.iloc[-2])
            change_pct = (close_today - close_prev) / close_prev * 100
            results[name] = {
                "price": round(close_today, 2),
                "change_pct": round(change_pct, 2),
                "symbol": symbol,
            }
        except Exception:
            pass
    return results


def get_sector_performance() -> dict:
    """Fetch sector ETF daily % changes for the heatmap."""
    tickers = list(SECTOR_ETFS.values())
    try:
        data = yf.download(tickers, period="2d", group_by="ticker", progress=False, auto_adjust=True)
    except Exception:
        return {}

    results = {}
    for name, symbol in SECTOR_ETFS.items():
        try:
            closes = data[symbol]["Close"].dropna()
            if len(closes) < 2:
                continue
            close_today = float(closes.iloc[-1])
            close_prev = float(closes.iloc[-2])
            change_pct = (close_today - close_prev) / close_prev * 100
            results[name] = round(change_pct, 2)
        except Exception:
            pass
    return results


def get_vix() -> dict:
    """Fetch VIX (fear gauge) and label its level."""
    try:
        vix = yf.Ticker("^VIX")
        info = vix.info
        price = float(
            info.get("regularMarketPrice")
            or info.get("currentPrice")
            or 0
        )
        if price == 0:
            # fallback: grab from recent download
            data = yf.download("^VIX", period="1d", progress=False)
            if not data.empty:
                price = float(data["Close"].iloc[-1])
    except Exception:
        price = 0.0

    if price < 15:
        label = "Extreme Low — Complacency"
    elif price < 20:
        label = "Low Fear — Calm Markets"
    elif price < 25:
        label = "Moderate — Normal Anxiety"
    elif price < 30:
        label = "Elevated Fear"
    else:
        label = "High Fear — Panic"

    return {"value": round(price, 2) if price else None, "label": label}


# ─────────────────────────────────────────────────────────
# Economic indicators (FRED)
# ─────────────────────────────────────────────────────────

INDICATORS = [
    {
        "name":       "CPI (Inflation)",
        "fred_id":    "CPIAUCSL",
        "unit":       "%",
        "is_pct_change": True,   # Report YoY % change instead of raw index
        "what":       "Measures how fast prices are rising for everyday goods — food, gas, rent. The Fed targets 2% annual inflation.",
        "why":        "High inflation = Fed keeps rates high = expensive borrowing = stocks struggle. Falling toward 2% = Fed can cut rates = stocks rally.",
        "bullish":    "Falling toward 2%",
        "bearish":    "Rising above 3%",
    },
    {
        "name":       "Unemployment Rate",
        "fred_id":    "UNRATE",
        "unit":       "%",
        "is_pct_change": False,
        "what":       "Percentage of people actively looking for work who can't find jobs.",
        "why":        "Rising unemployment = people spend less = lower company earnings. BUT it can also be bullish — it may push the Fed to cut rates to stimulate the economy.",
        "bullish":    "Stable around 3.5–4%",
        "bearish":    "Rapidly rising (signals recession)",
    },
    {
        "name":       "Fed Funds Rate",
        "fred_id":    "FEDFUNDS",
        "unit":       "%",
        "is_pct_change": False,
        "what":       "The interest rate banks charge each other overnight. The Fed's main tool to control the economy.",
        "why":        "When the Fed raises this, ALL borrowing gets more expensive — mortgages, car loans, business loans. Lower rates = cheaper money = more investment and spending.",
        "bullish":    "Rate cuts (or signals of future cuts)",
        "bearish":    "Rate hikes or 'higher for longer' signals",
    },
    {
        "name":       "GDP Growth (QoQ)",
        "fred_id":    "A191RL1Q225SBEA",
        "unit":       "%",
        "is_pct_change": False,
        "what":       "The annualized quarterly growth rate of the US economy. Two consecutive negative quarters = official recession.",
        "why":        "Growing GDP = healthy economy = companies earn more = stocks go up. Shrinking GDP = recession fears = stocks sell off.",
        "bullish":    "Steady 2–3% growth",
        "bearish":    "Negative or rapidly slowing",
    },
    {
        "name":       "10-Year Treasury Yield",
        "fred_id":    "DGS10",
        "unit":       "%",
        "is_pct_change": False,
        "what":       "What the US government pays to borrow money for 10 years. The benchmark 'risk-free' rate in finance.",
        "why":        "When yields rise, bonds compete with stocks for investor money. High yields make stocks less attractive because you can earn 'safe' returns in bonds instead.",
        "bullish":    "Falling (money flows into stocks)",
        "bearish":    "Rising above 4.5% (money flows to bonds)",
    },
    {
        "name":       "Consumer Confidence",
        "fred_id":    "UMCSENT",
        "unit":       "",
        "is_pct_change": False,
        "what":       "Survey measuring how optimistic regular people feel about the economy. Above 100 = optimistic.",
        "why":        "When people feel confident, they spend more. Consumer spending is ~70% of US GDP — high confidence drives sales, which drives earnings.",
        "bullish":    "Rising / above 100",
        "bearish":    "Falling sharply",
    },
]


def get_indicators(fred_api_key: Optional[str] = None) -> list[dict]:
    """
    Fetch latest economic indicator values from FRED.
    If no API key, returns indicators with educational content but no live values.
    """
    import requests as req

    fred_key = fred_api_key or os.environ.get("FRED_API_KEY")
    results = []

    for config in INDICATORS:
        entry = {
            "name":     config["name"],
            "unit":     config["unit"],
            "what":     config["what"],
            "why":      config["why"],
            "bullish":  config["bullish"],
            "bearish":  config["bearish"],
            "value":    None,
            "previous": None,
            "trend":    "?",
            "date":     None,
        }

        if fred_key:
            try:
                resp = req.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id":  config["fred_id"],
                        "api_key":    fred_key,
                        "file_type":  "json",
                        "sort_order": "desc",
                        "limit":      13,   # extra for YoY calc
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                obs = resp.json().get("observations", [])
                # Filter out non-numeric or missing values
                valid = [o for o in obs if o.get("value") not in (".", "", None)]
                if not valid:
                    results.append(entry)
                    continue

                if config.get("is_pct_change") and len(valid) >= 13:
                    # CPI: compute YoY % change
                    current_raw = float(valid[0]["value"])
                    year_ago_raw = float(valid[12]["value"])
                    current = round((current_raw - year_ago_raw) / year_ago_raw * 100, 2)
                    prev_raw = float(valid[1]["value"])
                    year_ago_prev = float(valid[13]["value"]) if len(valid) > 13 else year_ago_raw
                    previous = round((prev_raw - year_ago_prev) / year_ago_prev * 100, 2) if len(valid) > 13 else None
                else:
                    current = round(float(valid[0]["value"]), 2)
                    previous = round(float(valid[1]["value"]), 2) if len(valid) > 1 else None

                entry["value"] = current
                entry["previous"] = previous
                entry["date"] = valid[0]["date"]
                if previous is not None:
                    entry["trend"] = "▲" if current > previous else "▼" if current < previous else "—"
            except Exception:
                pass

        results.append(entry)

    return results


# ─────────────────────────────────────────────────────────
# AI market narrative (Claude)
# ─────────────────────────────────────────────────────────

def generate_market_story(
    market_data: dict,
    sector_data: dict,
    vix: dict,
    indicators: list,
) -> dict:
    """
    Use Claude to generate today's market narrative and event analysis.
    Returns a dict with market_story, sectors_to_watch, and events.
    """
    import anthropic

    client = anthropic.Anthropic()

    indicator_summary = [
        {"name": i["name"], "value": i["value"], "trend": i["trend"]}
        for i in indicators
    ]

    prompt = f"""You are a market analyst writing a daily briefing for a high school student who is learning about investing. Be clear, conversational, and educational.

Today's market data:
- Indices: {json.dumps(market_data)}
- Sector performance (%): {json.dumps(sector_data)}
- VIX (fear gauge): {json.dumps(vix)}
- Economic indicators: {json.dumps(indicator_summary)}
- Today's date: {datetime.now().strftime("%B %d, %Y")}

Write a briefing with these sections:

1. TODAY'S MARKET STORY (2-3 paragraphs)
   - What is happening in markets and why
   - Explain each concept simply (e.g., "Tech rallied because...")
   - Connect index moves to real-world events

2. SECTORS TO WATCH (3-4 bullet points)
   - Which sectors moved most and why
   - What to watch next week

3. CRISIS & EVENT MONITOR (2-3 current macro events)
   - Major events affecting markets right now
   - For each: what's happening, who it helps, who it hurts
   - Severity: red (major risk), yellow (watch closely), green (opportunity)

When you mention concepts like "rate cuts" or "tariffs", briefly explain what they mean. Use specific ticker symbols where relevant.

Respond ONLY with valid JSON in this exact format:
{{
    "market_story": "string with the full narrative",
    "sectors_to_watch": ["bullet 1", "bullet 2", "bullet 3"],
    "events": [
        {{
            "title": "short title",
            "severity": "red|yellow|green",
            "description": "2-3 sentence explanation at high school level",
            "helps": ["TICKER1"],
            "hurts": ["TICKER2"]
        }}
    ]
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]

    return json.loads(text.strip())
