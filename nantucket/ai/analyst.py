"""
Claude-powered market analyst.

This module uses the Anthropic API to generate:
1. Stock explanations — WHY is this stock moving?
2. Bull/bear cases — what are the arguments for and against?

Why Claude for market analysis?
- Markets are driven by narratives, not just numbers
- A P/E of 25 could be cheap or expensive depending on growth rate, sector,
  and macro conditions — context matters enormously
- Claude can connect earnings reports, Fed policy, sector rotation, and news
  into a coherent story that helps you understand what's really happening

Setup:
    Set the ANTHROPIC_API_KEY environment variable before using these functions:
    $ export ANTHROPIC_API_KEY=sk-ant-...

Web Search:
    When available, Claude uses its web search capability to pull in recent
    news and filings. This makes explanations much more current and specific.
    Falls back gracefully if web search isn't available.

Model used: claude-sonnet-4-5 (configurable via NANTUCKET_MODEL env var)
"""

from __future__ import annotations

import os
from typing import Optional

from nantucket.data.stocks import StockQuote, get_quote

# Default model — can be overridden with NANTUCKET_MODEL env var
DEFAULT_MODEL = "claude-sonnet-4-5"


def _get_client():
    """
    Create an Anthropic client.
    Raises a clear error if the API key isn't set.
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "\n[bold red]ANTHROPIC_API_KEY not set![/bold red]\n"
            "Get your key at https://console.anthropic.com/\n"
            "Then run: export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return anthropic.Anthropic(api_key=api_key)


def _get_model() -> str:
    return os.environ.get("NANTUCKET_MODEL", DEFAULT_MODEL)


def _format_stock_context(q: StockQuote) -> str:
    """
    Format a StockQuote into a text summary for the AI prompt.
    This gives Claude the raw numbers to work from.
    """
    lines = [
        f"Ticker: {q.ticker}",
        f"Name: {q.name}",
        f"Price: ${q.price:.2f}",
        f"Daily Change: {q.change_pct:+.2f}%",
        f"1-Week Change: {q.change_1w:+.2f}%",
        f"Volume: {q.volume:,} (Avg: {q.avg_volume:,}, Ratio: {q.volume_ratio:.2f}x)",
        f"Market Cap: ${q.market_cap / 1e9:.1f}B" if q.market_cap else "Market Cap: N/A",
        f"Sector: {q.sector or 'N/A'}",
        f"Industry: {q.industry or 'N/A'}",
    ]
    if q.pe_ratio is not None:
        lines.append(f"P/E Ratio (trailing): {q.pe_ratio:.1f}x")
    if q.pb_ratio is not None:
        lines.append(f"P/B Ratio: {q.pb_ratio:.1f}x")
    if q.eps is not None:
        lines.append(f"EPS (trailing 12mo): ${q.eps:.2f}")
    if q.dividend_yield:
        lines.append(f"Dividend Yield: {q.dividend_yield:.2f}%")
    if q.ma_50:
        lines.append(f"50-Day MA: ${q.ma_50:.2f} ({'above' if q.above_50ma else 'below'})")
    if q.ma_200:
        lines.append(f"200-Day MA: ${q.ma_200:.2f} ({'above' if q.above_200ma else 'below'})")
    lines.append(f"52-Week High: ${q.week_52_high:.2f} | Low: ${q.week_52_low:.2f}")
    if q.revenue_growth is not None:
        lines.append(f"Revenue Growth (YoY): {q.revenue_growth * 100:.1f}%")
    if q.earnings_growth is not None:
        lines.append(f"Earnings Growth (YoY): {q.earnings_growth * 100:.1f}%")

    return "\n".join(lines)


def _call_claude(prompt: str, use_web_search: bool = True) -> str:
    """
    Call the Claude API with optional web search.

    Tool use flow:
    1. Send prompt with web_search tool available
    2. Claude may call web_search to get current news
    3. We handle the tool result and continue the conversation
    4. Claude returns its final analysis

    Falls back to a plain call if web search isn't available.
    """
    import anthropic

    client = _get_client()
    model = _get_model()

    if use_web_search:
        try:
            response = client.beta.messages.create(
                model=model,
                max_tokens=2000,
                betas=["web-search-2025-03-05"],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            # Extract all text blocks from the response
            text_parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
            return "\n".join(text_parts).strip()

        except Exception:
            # Web search not available — fall back to plain call
            pass

    # Plain call without web search
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text_parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(text_parts).strip()


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def explain_stock(ticker: str) -> str:
    """
    Generate an AI explanation of what's happening with a stock and WHY.

    This is the "connect the dots" analysis — it takes raw numbers and
    explains the story behind them. What do recent price moves mean?
    What's driving the sector? How do macro conditions affect this company?

    Args:
        ticker: Stock symbol (e.g. "AAPL")

    Returns:
        A multi-paragraph analysis as a string (Markdown formatted)
    """
    q = get_quote(ticker)
    if q.error:
        return f"Could not fetch data for {ticker}: {q.error}"

    context = _format_stock_context(q)

    prompt = f"""You are a market analyst helping a high school student learn to invest.
They want to understand what's happening with {q.ticker} ({q.name}).

Here's the current data:
{context}

Please explain:

## What's Happening
Describe the recent price action in plain terms. Is this stock up or down recently?
How does that compare to the broader market? What does the volume tell us?

## Why It's Moving
Connect the dots: what's likely causing recent price action?
Consider: recent earnings/guidance, sector trends, macro conditions (Fed rates,
inflation, economic cycle), competitive landscape, and any major news.
Search for recent news about {q.ticker} to make this specific and current.

## Reading the Numbers
Explain what the key metrics (P/E, P/B, etc.) mean specifically for THIS company.
Is a P/E of X high or low for this sector? What would be concerning vs reassuring?

## What to Watch
Give 2-3 specific things to watch going forward that will tell us if the thesis
is playing out — earnings dates, key metrics, macro catalysts.

Tone: Intermediate level. The student knows what P/E means but wants to understand
the deeper "why." Connect macroeconomic dots to company performance. Be educational
and engaging, not dry. No buy/sell recommendations — just understanding.
"""

    return _call_claude(prompt, use_web_search=True)


def bull_bear_case(ticker: str) -> dict[str, list[str] | str]:
    """
    Generate a structured bull case vs bear case for a stock.

    Returns a dict with keys:
    - "bull": list of 3 bull arguments
    - "bear": list of 3 bear arguments
    - "risks": key risks to the bull case
    - "catalysts": upcoming events that could move the stock
    - "raw": full raw text from Claude

    The structured format is used for the dashboard display.
    """
    q = get_quote(ticker)
    if q.error:
        return {"error": f"Could not fetch data for {ticker}: {q.error}"}

    context = _format_stock_context(q)

    prompt = f"""You are a Wall Street analyst preparing a balanced research note on {q.ticker} ({q.name}).

Current data:
{context}

Search for recent news and developments about {q.ticker}.

Provide a structured bull vs bear analysis:

## 🐂 BULL CASE (3 arguments)
For each argument, give a 2-3 sentence explanation connecting the data point to
why it's positive for the stock. Be specific — reference actual metrics, trends, or events.

## 🐻 BEAR CASE (3 arguments)
For each argument, give a 2-3 sentence explanation of the risk or headwind.
Include both company-specific risks and macro/sector risks.

## ⚠️ KEY RISKS
What are the 2-3 biggest things that could blow up the bull case?
Focus on risks that aren't already priced in or obvious.

## 🎯 CATALYSTS TO WATCH
What upcoming events or data points could push the stock significantly in either direction?
(earnings dates, product launches, regulatory decisions, macro events, etc.)

Write for an intermediate-level investor who knows markets but wants to think deeper.
Be balanced — avoid being a cheerleader or a doom-monger. Markets are about probabilities.
"""

    raw = _call_claude(prompt, use_web_search=True)

    # Return both the structured text and the raw output
    return {
        "ticker": ticker,
        "name": q.name,
        "price": q.price,
        "change_pct": q.change_pct,
        "raw": raw,
    }
