"""
NANTUCKET — CLI entry point.

All commands are defined here using Typer. Rich handles the pretty output.

Command structure:
  nantucket screen       — screen stocks/ETFs with filters
  nantucket watch        — manage your watchlist (add/show/remove)
  nantucket trade        — log paper trades (buy/sell)
  nantucket portfolio    — view positions and P&L
  nantucket explain      — AI: why is this stock moving?
  nantucket bull-bear    — AI: bull case vs bear case
  nantucket dashboard    — start the web dashboard

Run `nantucket --help` or `nantucket COMMAND --help` for usage.
"""

from __future__ import annotations

import webbrowser
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

from nantucket.db.database import init_db

# Initialize DB on every invocation
init_db()

app = typer.Typer(
    name="nantucket",
    help="⚓  Personal investment screener and portfolio tracker.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
watch_app  = typer.Typer(help="Manage your watchlist.", no_args_is_help=True)
trade_app  = typer.Typer(help="Log paper trades.", no_args_is_help=True)
alerts_app = typer.Typer(help="Manage price alerts.", no_args_is_help=True)

app.add_typer(watch_app,  name="watch")
app.add_typer(trade_app,  name="trade")
app.add_typer(alerts_app, name="alerts")

console = Console()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_price(price: float) -> str:
    return f"${price:,.2f}" if price else "—"

def _fmt_pct(pct: float | None, show_sign: bool = True) -> Text:
    """Return a Rich Text object colored green/red for percent changes."""
    if pct is None:
        return Text("—", style="dim")
    sign = "+" if pct >= 0 and show_sign else ""
    color = "green" if pct >= 0 else "red"
    return Text(f"{sign}{pct:.2f}%", style=color)

def _fmt_change(val: float | None) -> Text:
    """Dollar change, colored green/red."""
    if val is None:
        return Text("—", style="dim")
    sign = "+" if val >= 0 else ""
    color = "green" if val >= 0 else "red"
    return Text(f"{sign}${val:,.2f}", style=color)

def _fmt_vol(vol: int) -> str:
    if not vol:
        return "—"
    if vol >= 1_000_000_000:
        return f"{vol/1e9:.1f}B"
    if vol >= 1_000_000:
        return f"{vol/1e6:.1f}M"
    if vol >= 1_000:
        return f"{vol/1e3:.0f}K"
    return str(vol)

def _fmt_mktcap(cap: float) -> str:
    if not cap:
        return "—"
    if cap >= 1e12:
        return f"${cap/1e12:.1f}T"
    if cap >= 1e9:
        return f"${cap/1e9:.1f}B"
    if cap >= 1e6:
        return f"${cap/1e6:.0f}M"
    return f"${cap:,.0f}"

def _parse_number(s: str | None) -> float | None:
    """Parse strings like '1M', '500K', '10B' into floats."""
    if s is None:
        return None
    s = s.upper().replace(",", "")
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return float(s)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket screen
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def screen(
    preset: Optional[str] = typer.Option(None, "--preset", "-p",
        help="Use a built-in preset: value, momentum, dividend, growth, oversold, volume_spike"),
    tickers: Optional[str] = typer.Option(None, "--tickers", "-t",
        help="Comma-separated list of specific tickers to screen, e.g. 'AAPL,MSFT,GOOGL'"),
    universe: str = typer.Option("top100", "--universe", "-u",
        help="Screening universe: top100 (fast) or sp500 (thorough)"),
    pe_below: Optional[float] = typer.Option(None, "--pe-below",    help="Max P/E ratio"),
    pe_above: Optional[float] = typer.Option(None, "--pe-above",    help="Min P/E ratio"),
    pb_below: Optional[float] = typer.Option(None, "--pb-below",    help="Max P/B ratio"),
    eps_above: Optional[float] = typer.Option(None, "--eps-above",  help="Min EPS"),
    div_above: Optional[float] = typer.Option(None, "--div-above",  help="Min dividend yield %"),
    mktcap: Optional[str]    = typer.Option(None, "--market-cap-above", help="Min market cap (e.g. 1B, 500M)"),
    vol_above: Optional[str] = typer.Option(None, "--volume-above", help="Min daily volume (e.g. 1M)"),
    vol_ratio: Optional[float] = typer.Option(None, "--volume-ratio-min", help="Min volume/avg-volume ratio"),
    change_above: Optional[float] = typer.Option(None, "--change-above", help="Min daily % change"),
    change_1w: Optional[float]  = typer.Option(None, "--change-1w-above", help="Min 1-week % change"),
    above_50ma: bool = typer.Option(False, "--above-50ma/--no-above-50ma", help="Must be above 50-day MA"),
    near_52wl: Optional[float] = typer.Option(None, "--near-52w-low", help="Within N% of 52-week low"),
    sector: Optional[str]    = typer.Option(None, "--sector",       help="Filter by sector"),
    rev_growth: Optional[float] = typer.Option(None, "--rev-growth-above", help="Min revenue growth (0.20 = 20%)"),
    limit: int = typer.Option(10, "--limit", "-n",                  help="Number of results to show"),
    save: Optional[str] = typer.Option(None, "--save",              help="Save this screen with a name"),
    list_presets: bool  = typer.Option(False, "--list-presets",     help="Show available presets and exit"),
):
    """
    Screen stocks and ETFs using filters or built-in presets.

    Examples:
      nantucket screen --preset value
      nantucket screen --pe-below 15 --sector Technology
      nantucket screen --preset momentum --universe sp500
      nantucket screen --save my-tech --pe-below 20 --sector Technology
    """
    from nantucket.screener import (
        ScreenFilters, run_screen, get_preset, list_presets as get_all_presets,
        get_preset_learning_note
    )

    if list_presets:
        presets = get_all_presets()
        t = Table(title="Available Presets", box=box.ROUNDED, border_style="dim")
        t.add_column("Name",        style="bold cyan",  width=14)
        t.add_column("Description", style="white")
        for name, desc in presets.items():
            t.add_row(name, desc)
        console.print(t)
        return

    # Build filters
    if preset:
        filters = get_preset(preset)
        if filters is None:
            console.print(f"[red]Unknown preset '{preset}'. Run --list-presets to see options.[/red]")
            raise typer.Exit(1)
    else:
        filters = ScreenFilters()

    # Apply CLI overrides
    if pe_below is not None:    filters.pe_max = pe_below
    if pe_above is not None:    filters.pe_min = pe_above
    if pb_below is not None:    filters.pb_max = pb_below
    if eps_above is not None:   filters.eps_min = eps_above
    if div_above is not None:   filters.div_yield_min = div_above
    if mktcap:                  filters.market_cap_min = _parse_number(mktcap)
    if vol_above:               filters.volume_min = _parse_number(vol_above)
    if vol_ratio is not None:   filters.volume_ratio_min = vol_ratio
    if change_above is not None: filters.change_pct_min = change_above
    if change_1w is not None:   filters.change_1w_min = change_1w
    if above_50ma:              filters.above_50ma = True
    if near_52wl is not None:   filters.near_52w_low = near_52wl
    if sector:                  filters.sector = sector
    if rev_growth is not None:  filters.revenue_growth_min = rev_growth
    filters.limit = limit

    # Parse explicit tickers
    ticker_list = [t.strip().upper() for t in tickers.split(",")] if tickers else None

    # Show learning note for presets
    if preset:
        note = get_preset_learning_note(preset)
        if note:
            console.print(Panel(f"[yellow]💡 {note}[/yellow]", title=f"[bold]{preset}[/bold]", border_style="yellow"))

    # Run the screen
    result = run_screen(filters, universe=universe, tickers=ticker_list, show_progress=True)

    if not result.quotes:
        console.print("[yellow]No stocks matched your filters. Try relaxing the criteria.[/yellow]")
        console.print(f"[dim]Checked {result.universe_size} tickers, {result.errors} had errors.[/dim]")
        raise typer.Exit(0)

    # Build results table
    t = Table(
        title=f"[bold]Screen Results[/bold]  [dim](preset: {preset or 'custom'} · {result.universe_size} checked · {len(result.quotes)} matched)[/dim]",
        box=box.ROUNDED,
        border_style="dim",
        show_lines=False,
    )
    t.add_column("#",         style="dim",          width=3,  justify="right")
    t.add_column("Ticker",    style="bold",          width=8)
    t.add_column("Name",      style="dim",           width=26, no_wrap=True)
    t.add_column("Price",                            width=10, justify="right")
    t.add_column("Day %",                            width=8,  justify="right")
    t.add_column("Week %",                           width=8,  justify="right")
    t.add_column("P/E",       style="dim",           width=7,  justify="right")
    t.add_column("Yield",     style="dim",           width=7,  justify="right")
    t.add_column("Vol Ratio", style="dim",           width=9,  justify="right")
    t.add_column("Mkt Cap",   style="dim",           width=9,  justify="right")
    t.add_column("Sector",    style="dim",           width=18, no_wrap=True)

    for i, q in enumerate(result.quotes, 1):
        name = (q.name[:24] + "…") if len(q.name) > 25 else q.name
        sector_short = (q.sector[:16] + "…") if len(q.sector) > 17 else q.sector
        t.add_row(
            str(i),
            q.ticker,
            name,
            _fmt_price(q.price),
            _fmt_pct(q.change_pct),
            _fmt_pct(q.change_1w),
            f"{q.pe_ratio:.1f}x" if q.pe_ratio else "—",
            f"{q.dividend_yield:.1f}%" if q.dividend_yield else "—",
            f"{q.volume_ratio:.1f}x" if q.volume_ratio else "—",
            _fmt_mktcap(q.market_cap),
            sector_short or "—",
        )

    console.print(t)
    console.print(f"[dim]  {result.errors} tickers had errors · Run [bold]nantucket explain TICKER[/bold] for AI analysis[/dim]")

    # Save the screen if requested
    if save:
        import json
        from nantucket.db.database import get_connection
        filters_json = json.dumps(filters.to_dict())
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO saved_screens (name, filters_json) VALUES (?, ?)",
                (save, filters_json),
            )
            conn.commit()
        console.print(f"[green]✓ Screen saved as '[bold]{save}[/bold]'. Use: nantucket screen --preset {save}[/green]")


# ──────────────────────────────────────────────────────────────────────────────
# nantucket watch
# ──────────────────────────────────────────────────────────────────────────────

@watch_app.command("add")
def watch_add(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols to add (e.g. AAPL MSFT BTC)"),
    asset_type: str = typer.Option("stock", "--type", "-t",
        help="Asset type: stock, etf, crypto, future"),
):
    """Add one or more tickers to your watchlist."""
    from nantucket.watchlist import add_ticker
    for ticker in tickers:
        # Auto-detect asset type if not specified
        detected_type = asset_type
        if asset_type == "stock":
            ticker_upper = ticker.upper()
            from nantucket.data.crypto import TICKER_TO_ID
            from nantucket.data.futures import FUTURES_UNIVERSE, ALIASES
            if ticker_upper in TICKER_TO_ID:
                detected_type = "crypto"
            elif ticker_upper in FUTURES_UNIVERSE or ticker_upper in ALIASES:
                detected_type = "future"

        added = add_ticker(ticker, asset_type=detected_type)
        if added:
            console.print(f"[green]✓ Added[/green] [bold]{ticker.upper()}[/bold] [dim]({detected_type})[/dim]")
        else:
            console.print(f"[yellow]  {ticker.upper()} is already in your watchlist[/yellow]")


@watch_app.command("remove")
def watch_remove(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols to remove"),
):
    """Remove one or more tickers from your watchlist."""
    from nantucket.watchlist import remove_ticker
    for ticker in tickers:
        removed = remove_ticker(ticker)
        if removed:
            console.print(f"[red]✗ Removed[/red] [bold]{ticker.upper()}[/bold]")
        else:
            console.print(f"[yellow]  {ticker.upper()} was not in your watchlist[/yellow]")


@watch_app.command("show")
def watch_show():
    """Show current prices and stats for all watchlist tickers."""
    from nantucket.watchlist import get_watchlist, get_watchlist_quotes
    entries = get_watchlist()
    if not entries:
        console.print("[yellow]Your watchlist is empty.[/yellow]")
        console.print("[dim]Add tickers: nantucket watch add AAPL MSFT BTC[/dim]")
        return

    console.print(f"[dim]Fetching quotes for {len(entries)} tickers...[/dim]")
    quotes = get_watchlist_quotes(show_progress=True)

    t = Table(
        title=f"[bold]Watchlist[/bold]  [dim]({len(quotes)} tickers)[/dim]",
        box=box.ROUNDED,
        border_style="dim",
    )
    t.add_column("Ticker",  style="bold",  width=8)
    t.add_column("Name",    style="dim",   width=28, no_wrap=True)
    t.add_column("Price",                  width=10, justify="right")
    t.add_column("Day %",                  width=9,  justify="right")
    t.add_column("Week %",                 width=9,  justify="right")
    t.add_column("Volume",  style="dim",   width=9,  justify="right")
    t.add_column("P/E",     style="dim",   width=7,  justify="right")
    t.add_column("Mkt Cap", style="dim",   width=9,  justify="right")
    t.add_column("Type",    style="dim",   width=7)

    for q in quotes:
        if q.error:
            t.add_row(
                q.ticker, "[red]Error fetching data[/red]",
                "—", "—", "—", "—", "—", "—", q.asset_type,
            )
            continue
        name = (q.name[:26] + "…") if len(q.name) > 27 else q.name
        t.add_row(
            q.ticker,
            name,
            _fmt_price(q.price),
            _fmt_pct(q.change_pct),
            _fmt_pct(q.change_1w),
            _fmt_vol(q.volume),
            f"{q.pe_ratio:.1f}x" if q.pe_ratio else "—",
            _fmt_mktcap(q.market_cap),
            q.asset_type,
        )

    console.print(t)
    console.print(f"[dim]  Run [bold]nantucket explain TICKER[/bold] for AI analysis on any ticker[/dim]")


@watch_app.command("clear")
def watch_clear(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Remove all tickers from your watchlist."""
    from nantucket.watchlist import clear_watchlist
    if not confirm:
        typer.confirm("Clear your entire watchlist?", abort=True)
    count = clear_watchlist()
    console.print(f"[red]Cleared {count} tickers from watchlist.[/red]")


# ──────────────────────────────────────────────────────────────────────────────
# nantucket trade
# ──────────────────────────────────────────────────────────────────────────────

@trade_app.command("buy")
def trade_buy(
    ticker: str   = typer.Argument(..., help="Ticker symbol, e.g. AAPL"),
    quantity: float = typer.Argument(..., help="Number of shares/units to buy"),
    price: Optional[float] = typer.Option(None, "--price", "-p",
        help="Price per share (defaults to current market price)"),
    asset_type: str = typer.Option("stock", "--type", help="Asset type: stock, etf, crypto, future"),
):
    """
    Log a paper buy trade.

    Examples:
      nantucket trade buy AAPL 10 --price 185.50
      nantucket trade buy BTC 0.1
      nantucket trade buy NVDA 5
    """
    from nantucket.portfolio import record_buy
    from nantucket.data.stocks import get_quote

    ticker = ticker.upper()

    # Fetch current price if not specified
    if price is None:
        console.print(f"[dim]Fetching current price for {ticker}...[/dim]")
        q = get_quote(ticker)
        if q.error or q.price == 0:
            console.print(f"[red]Could not fetch price for {ticker}: {q.error}[/red]")
            console.print("[dim]Use --price to specify a manual price.[/dim]")
            raise typer.Exit(1)
        price = q.price
        console.print(f"[dim]Using current price: ${price:.2f}[/dim]")

    try:
        trade_id = record_buy(ticker, quantity, price, asset_type)
        total = quantity * price
        console.print(
            f"[green]✓ BUY[/green]  "
            f"[bold]{quantity:g}[/bold] × [bold]{ticker}[/bold] "
            f"@ [bold]${price:.2f}[/bold]  "
            f"[dim]= ${total:,.2f} total  (trade #{trade_id})[/dim]"
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@trade_app.command("sell")
def trade_sell(
    ticker: str   = typer.Argument(..., help="Ticker symbol"),
    quantity: float = typer.Argument(..., help="Number of shares/units to sell"),
    price: Optional[float] = typer.Option(None, "--price", "-p",
        help="Price per share (defaults to current market price)"),
):
    """
    Log a paper sell trade.

    Examples:
      nantucket trade sell AAPL 5
      nantucket trade sell AAPL 5 --price 200.00
    """
    from nantucket.portfolio import record_sell
    from nantucket.data.stocks import get_quote

    ticker = ticker.upper()

    if price is None:
        console.print(f"[dim]Fetching current price for {ticker}...[/dim]")
        q = get_quote(ticker)
        if q.error or q.price == 0:
            console.print(f"[red]Could not fetch price for {ticker}: {q.error}[/red]")
            raise typer.Exit(1)
        price = q.price
        console.print(f"[dim]Using current price: ${price:.2f}[/dim]")

    try:
        trade_id = record_sell(ticker, quantity, price)
        total = quantity * price
        console.print(
            f"[red]✗ SELL[/red]  "
            f"[bold]{quantity:g}[/bold] × [bold]{ticker}[/bold] "
            f"@ [bold]${price:.2f}[/bold]  "
            f"[dim]= ${total:,.2f} total  (trade #{trade_id})[/dim]"
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@trade_app.command("history")
def trade_history(
    ticker: Optional[str] = typer.Argument(None, help="Filter to a specific ticker"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of trades to show"),
):
    """Show trade history."""
    from nantucket.portfolio import get_trade_history
    trades = get_trade_history(ticker=ticker, limit=limit)
    if not trades:
        console.print("[yellow]No trades recorded yet.[/yellow]")
        console.print("[dim]Try: nantucket trade buy AAPL 10 --price 185[/dim]")
        return

    t = Table(
        title=f"[bold]Trade History[/bold]  [dim]({'all tickers' if not ticker else ticker})[/dim]",
        box=box.ROUNDED, border_style="dim",
    )
    t.add_column("ID",        width=5,  justify="right", style="dim")
    t.add_column("Time",      width=20, style="dim")
    t.add_column("Action",    width=6)
    t.add_column("Ticker",    width=8,  style="bold")
    t.add_column("Qty",       width=10, justify="right")
    t.add_column("Price",     width=10, justify="right")
    t.add_column("Total",     width=12, justify="right")
    t.add_column("Type",      width=8,  style="dim")

    for trade in trades:
        action_text = Text("BUY", style="green") if trade.action == "buy" else Text("SELL", style="red")
        t.add_row(
            str(trade.id),
            trade.timestamp[:19],
            action_text,
            trade.ticker,
            f"{trade.quantity:g}",
            f"${trade.price:,.2f}",
            f"${trade.quantity * trade.price:,.2f}",
            trade.asset_type,
        )

    console.print(t)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket portfolio
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def portfolio():
    """Show all open positions with current P&L."""
    from nantucket.portfolio import get_portfolio_summary

    console.print("[dim]Fetching current prices...[/dim]")
    summary = get_portfolio_summary()

    if not summary.positions:
        console.print("[yellow]No open positions.[/yellow]")
        console.print("[dim]Log a trade: nantucket trade buy AAPL 10 --price 185[/dim]")
        return

    # Positions table
    t = Table(
        title="[bold]Portfolio[/bold]",
        box=box.ROUNDED,
        border_style="dim",
    )
    t.add_column("Ticker",    style="bold",   width=8)
    t.add_column("Type",      style="dim",    width=7)
    t.add_column("Qty",                       width=10, justify="right")
    t.add_column("Avg Cost",  style="dim",    width=10, justify="right")
    t.add_column("Current",                   width=10, justify="right")
    t.add_column("Value",                     width=12, justify="right")
    t.add_column("P&L $",                     width=12, justify="right")
    t.add_column("P&L %",                     width=9,  justify="right")
    t.add_column("Alloc",     style="dim",    width=7,  justify="right")

    for p in summary.positions:
        qty_str = f"{p.quantity:g}" if p.quantity == int(p.quantity) else f"{p.quantity:.4f}"
        t.add_row(
            p.ticker,
            p.asset_type,
            qty_str,
            _fmt_price(p.avg_cost),
            _fmt_price(p.current_price),
            _fmt_price(p.current_value),
            _fmt_change(p.unrealized_pnl),
            _fmt_pct(p.unrealized_pnl_pct),
            f"{p.allocation_pct:.1f}%",
        )

    console.print(t)

    # Summary footer
    pnl_color = "green" if summary.total_pnl >= 0 else "red"
    pnl_sign = "+" if summary.total_pnl >= 0 else ""
    console.print(
        f"\n  [bold]Total Value:[/bold]  {_fmt_price(summary.total_value)}"
        f"   [bold]Cost:[/bold]  {_fmt_price(summary.total_cost)}"
        f"   [bold]P&L:[/bold]  [{pnl_color}]{pnl_sign}${summary.total_pnl:,.2f}  "
        f"({pnl_sign}{summary.total_pnl_pct:.2f}%)[/{pnl_color}]"
    )
    console.print(f"  [dim]Run [bold]nantucket dashboard[/bold] for charts and visual P&L[/dim]")


# ──────────────────────────────────────────────────────────────────────────────
# nantucket explain
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def explain(
    ticker: str = typer.Argument(..., help="Stock ticker to analyze, e.g. AAPL"),
):
    """
    [AI] Explain why a stock is moving — connect the dots between data and news.

    Requires: ANTHROPIC_API_KEY environment variable.
    """
    console.print(f"[dim]Analyzing {ticker.upper()} with Claude... (this may take 20-30s)[/dim]")
    try:
        from nantucket.ai.analyst import explain_stock
        result = explain_stock(ticker.upper())
        console.print(Panel(
            Markdown(result),
            title=f"[bold cyan]{ticker.upper()} — AI Analysis[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        ))
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket bull-bear
# ──────────────────────────────────────────────────────────────────────────────

@app.command(name="bull-bear")
def bull_bear(
    ticker: str = typer.Argument(..., help="Stock ticker, e.g. TSLA"),
):
    """
    [AI] Generate bull case vs bear case arguments for a stock.

    Requires: ANTHROPIC_API_KEY environment variable.
    """
    console.print(f"[dim]Building bull/bear case for {ticker.upper()}...[/dim]")
    try:
        from nantucket.ai.analyst import bull_bear_case
        result = bull_bear_case(ticker.upper())
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
            raise typer.Exit(1)

        console.print(Panel(
            Markdown(result["raw"]),
            title=f"[bold]{ticker.upper()} — Bull vs Bear[/bold]  [dim]@ ${result['price']:.2f}[/dim]",
            border_style="yellow",
            padding=(1, 2),
        ))
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket dashboard
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
):
    """
    Start the web dashboard and open it in your browser.

    Dashboard features:
    - Overview with watchlist + portfolio snapshot
    - Interactive screener with filter UI
    - Portfolio charts (allocation pie, P&L bar)
    - Stock detail pages with price charts + AI analysis
    """
    import uvicorn

    url = f"http://{host}:{port}"
    console.print(Panel(
        f"[bold]NANTUCKET Dashboard[/bold]\n\n"
        f"[green]→[/green] Starting at [bold cyan]{url}[/bold cyan]\n"
        f"[dim]Press Ctrl+C to stop[/dim]",
        border_style="cyan",
    ))

    if not no_browser:
        import threading
        import time
        def _open_browser():
            time.sleep(1.5)  # Give uvicorn a moment to start
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "nantucket.dashboard.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning",  # Suppress verbose uvicorn logs
    )


# ──────────────────────────────────────────────────────────────────────────────
# nantucket quote (bonus: quick single-ticker quote)
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def quote(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols, e.g. AAPL MSFT BTC"),
):
    """Get a quick quote for one or more tickers."""
    from nantucket.data.stocks import get_quotes_batch

    tickers_upper = [t.upper() for t in tickers]
    console.print(f"[dim]Fetching {len(tickers_upper)} quote(s)...[/dim]")
    quotes_dict = get_quotes_batch(tickers_upper, max_workers=10)

    t = Table(box=box.ROUNDED, border_style="dim", show_header=True)
    t.add_column("Ticker",   style="bold",  width=8)
    t.add_column("Name",     style="dim",   width=30, no_wrap=True)
    t.add_column("Price",                   width=10, justify="right")
    t.add_column("Change $",                width=10, justify="right")
    t.add_column("Change %",                width=9,  justify="right")
    t.add_column("Volume",   style="dim",   width=9,  justify="right")
    t.add_column("Mkt Cap",  style="dim",   width=9,  justify="right")

    for ticker in tickers_upper:
        q = quotes_dict.get(ticker)
        if not q or q.error:
            t.add_row(ticker, f"[red]{q.error if q else 'No data'}[/red]",
                      "—", "—", "—", "—", "—")
            continue
        name = (q.name[:28] + "…") if len(q.name) > 29 else q.name
        t.add_row(
            q.ticker, name,
            _fmt_price(q.price),
            _fmt_change(q.change),
            _fmt_pct(q.change_pct),
            _fmt_vol(q.volume),
            _fmt_mktcap(q.market_cap),
        )

    console.print(t)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket movers
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def movers(
    limit: int  = typer.Option(10, "--limit", "-n",  help="Number of movers to show per category"),
    type: str   = typer.Option("all", "--type", "-t", help="gainers | losers | active | all"),
):
    """Show top S&P 500 market movers (gainers, losers, most active)."""
    from nantucket.data.stocks import get_quotes_batch, TOP_100_SP500

    console.print("[dim]Fetching S&P 100 quotes...[/dim]")
    quotes_dict = get_quotes_batch(list(TOP_100_SP500), max_workers=8)
    all_quotes = [q for q in quotes_dict.values() if not q.error and q.price > 0]

    gainers = sorted(all_quotes, key=lambda q: q.change_pct or 0, reverse=True)[:limit]
    losers  = sorted(all_quotes, key=lambda q: q.change_pct or 0)[:limit]
    active  = sorted(all_quotes, key=lambda q: q.volume_ratio or 0, reverse=True)[:limit]

    def _movers_table(title: str, rows: list, sort_col: str = "change_pct") -> Table:
        t = Table(title=title, box=box.ROUNDED, border_style="dim")
        t.add_column("#",       width=3,  justify="right", style="dim")
        t.add_column("Ticker",  width=8,  style="bold")
        t.add_column("Name",    width=26, style="dim", no_wrap=True)
        t.add_column("Price",   width=10, justify="right")
        t.add_column("Day %",   width=9,  justify="right")
        t.add_column("Volume",  width=9,  justify="right", style="dim")
        t.add_column("Mkt Cap", width=9,  justify="right", style="dim")
        for i, q in enumerate(rows, 1):
            name = (q.name[:24] + "…") if len(q.name) > 25 else q.name
            t.add_row(
                str(i), q.ticker, name,
                _fmt_price(q.price),
                _fmt_pct(q.change_pct),
                _fmt_vol(q.volume),
                _fmt_mktcap(q.market_cap),
            )
        return t

    show = type.lower()
    if show in ("all", "gainers"):
        console.print(_movers_table("[bold green]Top Gainers[/bold green]", gainers))
    if show in ("all", "losers"):
        console.print(_movers_table("[bold red]Top Losers[/bold red]", losers))
    if show in ("all", "active"):
        t = Table(title="[bold yellow]Most Active[/bold yellow]", box=box.ROUNDED, border_style="dim")
        t.add_column("#",         width=3,  justify="right", style="dim")
        t.add_column("Ticker",    width=8,  style="bold")
        t.add_column("Name",      width=26, style="dim", no_wrap=True)
        t.add_column("Price",     width=10, justify="right")
        t.add_column("Day %",     width=9,  justify="right")
        t.add_column("Vol Ratio", width=10, justify="right")
        t.add_column("Volume",    width=9,  justify="right", style="dim")
        for i, q in enumerate(active, 1):
            name = (q.name[:24] + "…") if len(q.name) > 25 else q.name
            t.add_row(
                str(i), q.ticker, name,
                _fmt_price(q.price),
                _fmt_pct(q.change_pct),
                f"{q.volume_ratio:.1f}x" if q.volume_ratio else "—",
                _fmt_vol(q.volume),
            )
        console.print(t)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket earnings
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def earnings(
    days: int = typer.Option(14, "--days", "-d", help="Look-ahead window in days"),
):
    """Show upcoming earnings dates for your watchlist tickers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import date, timedelta
    import yfinance as yf
    from nantucket.watchlist import get_watchlist

    entries = get_watchlist()
    stock_tickers = [e["ticker"] for e in entries if e["asset_type"] in ("stock", "etf")]
    if not stock_tickers:
        console.print("[yellow]No stocks/ETFs in your watchlist.[/yellow]")
        return

    today = date.today()
    cutoff = today + timedelta(days=days)
    console.print(f"[dim]Checking earnings for {len(stock_tickers)} tickers...[/dim]")

    results: list[dict] = []

    def _check(ticker: str) -> None:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                return
            # calendar is a dict with key 'Earnings Date' containing a list of dates
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                if not isinstance(dates, list):
                    dates = [dates]
            else:
                return
            for ed in dates:
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

    if not results:
        console.print(f"[yellow]No earnings scheduled in the next {days} days for your watchlist.[/yellow]")
        return

    results.sort(key=lambda r: r["date"])
    t = Table(
        title=f"[bold]Upcoming Earnings[/bold]  [dim](next {days} days)[/dim]",
        box=box.ROUNDED, border_style="dim",
    )
    t.add_column("Ticker",     style="bold", width=8)
    t.add_column("Date",       width=12)
    t.add_column("Days Away",  width=10, justify="right")

    for r in results:
        days_text = Text(str(r["days_away"]), style="green" if r["days_away"] <= 3 else "")
        t.add_row(r["ticker"], r["date"], days_text)

    console.print(t)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket yield-curve
# ──────────────────────────────────────────────────────────────────────────────

@app.command(name="yield-curve")
def yield_curve():
    """Display the current US Treasury yield curve."""
    from nantucket.data.treasury import get_yield_curve

    console.print("[dim]Fetching Treasury yields...[/dim]")
    curve = get_yield_curve()

    if curve.error and not curve.points:
        console.print(f"[red]Error: {curve.error}[/red]")
        raise typer.Exit(1)

    t = Table(
        title="[bold]US Treasury Yield Curve[/bold]",
        box=box.ROUNDED, border_style="dim",
    )
    t.add_column("Maturity",  width=12)
    t.add_column("Yield %",   width=10, justify="right")
    t.add_column("1D Chg (bps)", width=14, justify="right")

    for p in curve.points:
        bps_text = _fmt_pct(p.change_bps / 100) if p.change_bps != 0 else Text("—", style="dim")
        t.add_row(p.label, f"{p.yield_pct:.3f}%", bps_text)

    console.print(t)

    spread_color = "red" if curve.is_inverted else "green"
    console.print(f"\n  [bold]10Y–3M Spread:[/bold] [{spread_color}]{curve.spread_10y3m:+.1f} bps[/{spread_color}]", end="")
    if curve.is_inverted:
        console.print("  [bold red]⚠ INVERTED[/bold red]")
    else:
        console.print()


# ──────────────────────────────────────────────────────────────────────────────
# nantucket news
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def news(
    ticker: Optional[str] = typer.Argument(None, help="Filter to a specific ticker (optional)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of headlines to show"),
):
    """Show market news headlines for your watchlist (or a specific ticker)."""
    from nantucket.data.news import get_news_for_tickers, get_market_news
    from nantucket.watchlist import get_watchlist

    if ticker:
        tickers = [ticker.upper()]
        console.print(f"[dim]Fetching news for {ticker.upper()}...[/dim]")
        feed = get_news_for_tickers(tickers, limit=limit)
    else:
        entries = get_watchlist()
        tickers = [e["ticker"] for e in entries]
        if tickers:
            console.print(f"[dim]Fetching news for {len(tickers)} watchlist tickers...[/dim]")
            feed = get_news_for_tickers(tickers, limit=limit)
        else:
            console.print("[dim]Fetching general market news...[/dim]")
            feed = get_market_news(limit=limit)

    if feed.error:
        console.print(f"[yellow]{feed.error}[/yellow]")
        return

    if not feed.items:
        console.print("[yellow]No matching headlines found.[/yellow]")
        return

    t = Table(
        title=f"[bold]News[/bold]  [dim]({len(feed.items)} headlines)[/dim]",
        box=box.ROUNDED, border_style="dim",
    )
    t.add_column("Ticker",   width=8,  style="bold")
    t.add_column("Source",   width=14, style="dim")
    t.add_column("Headline", width=60, no_wrap=True)

    for item in feed.items:
        headline = (item.headline[:57] + "…") if len(item.headline) > 58 else item.headline
        t.add_row(item.ticker or "MKT", item.source, headline)

    console.print(t)


# ──────────────────────────────────────────────────────────────────────────────
# nantucket analytics
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def analytics(
    period: str = typer.Option("1y", "--period", "-p", help="Analysis period: 6mo, 1y, 2y"),
):
    """Show portfolio risk/return analytics: Sharpe, drawdown, beta, and more."""
    from nantucket.portfolio import get_portfolio_summary
    from nantucket.analytics import get_portfolio_analytics

    console.print("[dim]Computing portfolio analytics (fetching price history)...[/dim]")
    summary = get_portfolio_summary()

    if not summary.positions:
        console.print("[yellow]No open positions. Log some trades first.[/yellow]")
        raise typer.Exit(0)

    result = get_portfolio_analytics(summary.positions, period=period)

    if result.error and result.sharpe_ratio is None:
        console.print(f"[red]Error: {result.error}[/red]")
        raise typer.Exit(1)

    def _fmt_metric(val: float | None, suffix: str = "") -> Text:
        if val is None:
            return Text("N/A", style="dim")
        color = "green" if val >= 0 else "red"
        return Text(f"{val:+.3f}{suffix}", style=color)

    t = Table(
        title=f"[bold]Portfolio Analytics[/bold]  [dim]({period} period · {len(summary.positions)} positions)[/dim]",
        box=box.ROUNDED, border_style="dim", show_header=False,
    )
    t.add_column("Metric", style="bold", width=22)
    t.add_column("Value",  width=20)
    t.add_column("Note",   style="dim", width=40)

    t.add_row("Total Return",      _fmt_metric(result.total_return, "%"),     "Period return on cost basis")
    t.add_row("Ann. Volatility",   Text(f"{result.volatility_annualized:.2f}%", style=""), "Annualised daily std deviation")
    t.add_row("Sharpe Ratio",      _fmt_metric(result.sharpe_ratio),          ">1.0 good, >2.0 excellent")
    t.add_row("Sortino Ratio",     _fmt_metric(result.sortino_ratio),         "Sharpe using downside vol only")
    t.add_row("Max Drawdown",      _fmt_metric(result.max_drawdown, "%"),     f"{result.max_drawdown_start} → {result.max_drawdown_end}")
    t.add_row("Beta vs SPY",       _fmt_metric(result.beta_vs_spy),           "<1 = less volatile than market")
    t.add_row("Alpha vs SPY",      _fmt_metric(result.alpha_vs_spy, "%/yr"),  "Excess return over benchmark")

    console.print(t)
    if result.error:
        console.print(f"[yellow]Note: {result.error}[/yellow]")


# ──────────────────────────────────────────────────────────────────────────────
# nantucket alerts
# ──────────────────────────────────────────────────────────────────────────────

@alerts_app.command("add")
def alerts_add(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. AAPL"),
    above: Optional[float] = typer.Option(None, "--above", help="Fire when price rises above this level"),
    below: Optional[float] = typer.Option(None, "--below", help="Fire when price falls below this level"),
    note:  str  = typer.Option("",   "--note",  "-n", help="Optional note for this alert"),
):
    """Add a price alert for a ticker."""
    from nantucket.alerts import add_alert

    if above is not None and below is not None:
        console.print("[red]Specify only one of --above or --below.[/red]")
        raise typer.Exit(1)
    if above is None and below is None:
        console.print("[red]Specify either --above PRICE or --below PRICE.[/red]")
        raise typer.Exit(1)

    direction = "above" if above is not None else "below"
    target    = above if above is not None else below

    alert = add_alert(ticker.upper(), direction, target, note)
    dir_color = "green" if direction == "above" else "red"
    console.print(
        f"[{dir_color}]✓[/{dir_color}] Alert set: [bold]{ticker.upper()}[/bold] "
        f"[{dir_color}]{direction} ${target:,.2f}[/{dir_color}]"
        + (f"  [dim]{note}[/dim]" if note else "")
        + f"  [dim](id: {alert.id})[/dim]"
    )


@alerts_app.command("show")
def alerts_show(
    ticker: Optional[str] = typer.Argument(None, help="Filter to a specific ticker"),
    all: bool = typer.Option(False, "--all", help="Include triggered alerts"),
):
    """Show price alerts."""
    from nantucket.alerts import get_alerts, get_recent_triggers

    alerts = get_alerts(ticker=ticker, active_only=not all)

    if not alerts:
        console.print("[yellow]No alerts found.[/yellow]")
        console.print("[dim]Add one: nantucket alerts add AAPL --above 200[/dim]")
        return

    t = Table(
        title=f"[bold]Price Alerts[/bold]  [dim]({'all' if all else 'active only'})[/dim]",
        box=box.ROUNDED, border_style="dim",
    )
    t.add_column("ID",        width=5,  justify="right", style="dim")
    t.add_column("Ticker",    width=8,  style="bold")
    t.add_column("Direction", width=8)
    t.add_column("Target",    width=12, justify="right")
    t.add_column("Note",      width=20, style="dim")
    t.add_column("Status",    width=10)
    t.add_column("Created",   width=12, style="dim")

    for a in alerts:
        dir_color = "green" if a.direction == "above" else "red"
        status = Text("active", style="green") if a.active else Text("triggered", style="dim")
        t.add_row(
            str(a.id),
            a.ticker,
            Text(a.direction, style=dir_color),
            f"${a.target:,.2f}",
            a.note or "—",
            status,
            a.created_at[:10],
        )

    console.print(t)


@alerts_app.command("remove")
def alerts_remove(
    alert_id: int = typer.Argument(..., help="Alert ID to remove (see nantucket alerts show)"),
):
    """Remove a price alert by ID."""
    from nantucket.alerts import remove_alert

    removed = remove_alert(alert_id)
    if removed:
        console.print(f"[red]✗ Removed alert #{alert_id}[/red]")
    else:
        console.print(f"[yellow]Alert #{alert_id} not found.[/yellow]")


if __name__ == "__main__":
    app()
