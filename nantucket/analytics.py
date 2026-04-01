"""
Portfolio analytics: Sharpe ratio, Sortino ratio, max drawdown, beta vs SPY,
correlation matrix — all computed from historical price data.

Uses get_history() from stocks.py (already 24-hour cached) so no extra
network calls are made when the data is warm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt
from typing import Optional, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from nantucket.portfolio import Position


@dataclass
class PortfolioAnalytics:
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown: float = 0.0           # negative percent, e.g. -23.5
    max_drawdown_start: str = ""
    max_drawdown_end: str = ""
    beta_vs_spy: Optional[float] = None
    alpha_vs_spy: Optional[float] = None
    volatility_annualized: float = 0.0
    total_return: float = 0.0
    period: str = "1y"
    error: Optional[str] = None


@dataclass
class CorrelationMatrix:
    tickers: list[str] = field(default_factory=list)
    matrix: list[list[float]] = field(default_factory=list)
    dates_range: str = ""
    excluded: list[str] = field(default_factory=list)   # tickers with < 30 points
    error: Optional[str] = None


def _build_portfolio_series(positions: list["Position"], period: str = "1y") -> Optional[pd.Series]:
    """
    Build a daily portfolio-value series by summing each position's
    historical close × quantity, aligned on common trading dates.
    Returns None if insufficient data.
    """
    from nantucket.data.stocks import get_history

    frames: list[pd.Series] = []
    for pos in positions:
        try:
            hist = get_history(pos.ticker, period=period, outputsize="full")
            if hist is None or hist.empty:
                continue
            close = hist["Close"].astype(float)
            close.index = pd.to_datetime(close.index)
            frames.append(close * pos.quantity)
        except Exception:
            continue

    if not frames:
        return None

    combined = pd.concat(frames, axis=1).fillna(method="ffill").dropna()
    if combined.empty:
        return None
    return combined.sum(axis=1)


def get_portfolio_analytics(
    positions: list["Position"],
    period: str = "1y",
) -> PortfolioAnalytics:
    """
    Compute risk/return metrics for the current portfolio.
    Requires at least 2 positions with price history.
    """
    from nantucket.data.stocks import get_history

    if not positions:
        return PortfolioAnalytics(error="No open positions in portfolio")

    try:
        portfolio_series = _build_portfolio_series(positions, period)
        if portfolio_series is None or len(portfolio_series) < 30:
            return PortfolioAnalytics(error="Insufficient price history (need ≥ 30 trading days)")

        returns = portfolio_series.pct_change().dropna()

        # ── Volatility ─────────────────────────────────────────────────────
        vol_ann = round(float(returns.std()) * sqrt(252) * 100, 2)

        # ── Sharpe (risk-free = 0) ──────────────────────────────────────────
        sharpe = round(float(returns.mean()) / float(returns.std()) * sqrt(252), 3) if returns.std() > 0 else None

        # ── Sortino ────────────────────────────────────────────────────────
        downside = returns[returns < 0]
        sortino = None
        if len(downside) > 0 and float(downside.std()) > 0:
            sortino = round(float(returns.mean()) / float(downside.std()) * sqrt(252), 3)

        # ── Max drawdown ───────────────────────────────────────────────────
        cumulative = (1 + returns).cumprod()
        rolling_max = cumulative.cummax()
        drawdown = (cumulative - rolling_max) / rolling_max
        max_dd = float(drawdown.min())
        dd_end_idx = drawdown.idxmin()
        # Peak is the last point where rolling_max == cumulative before dd_end
        pre_dd = cumulative[:dd_end_idx]
        dd_start_idx = pre_dd.idxmax() if not pre_dd.empty else dd_end_idx
        max_dd_pct = round(max_dd * 100, 2)
        dd_start = str(dd_start_idx)[:10]
        dd_end = str(dd_end_idx)[:10]

        # ── Total return ───────────────────────────────────────────────────
        total_return = round((float(cumulative.iloc[-1]) - 1) * 100, 2)

        # ── Beta & Alpha vs SPY ────────────────────────────────────────────
        beta = None
        alpha = None
        try:
            spy_hist = get_history("SPY", period=period, outputsize="full")
            if spy_hist is not None and not spy_hist.empty:
                spy_close = spy_hist["Close"].astype(float)
                spy_close.index = pd.to_datetime(spy_close.index)
                spy_returns = spy_close.pct_change().dropna()

                combined = pd.concat(
                    [returns.rename("port"), spy_returns.rename("spy")],
                    axis=1,
                ).dropna()

                if len(combined) >= 30:
                    cov = float(combined["port"].cov(combined["spy"]))
                    spy_var = float(combined["spy"].var())
                    if spy_var > 0:
                        beta = round(cov / spy_var, 3)
                        alpha_daily = float(combined["port"].mean()) - beta * float(combined["spy"].mean())
                        alpha = round(alpha_daily * 252 * 100, 2)  # annualized %
        except Exception:
            pass

        return PortfolioAnalytics(
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=max_dd_pct,
            max_drawdown_start=dd_start,
            max_drawdown_end=dd_end,
            beta_vs_spy=beta,
            alpha_vs_spy=alpha,
            volatility_annualized=vol_ann,
            total_return=total_return,
            period=period,
        )

    except Exception as exc:
        return PortfolioAnalytics(error=str(exc))


def get_correlation_matrix(tickers: list[str], period: str = "1y") -> CorrelationMatrix:
    """
    Compute pairwise return correlations for the given tickers.
    Tickers with fewer than 30 data points are excluded.
    """
    from nantucket.data.stocks import get_history

    if len(tickers) < 2:
        return CorrelationMatrix(error="Need at least 2 tickers for a correlation matrix")

    series_map: dict[str, pd.Series] = {}
    excluded: list[str] = []

    for ticker in tickers:
        try:
            hist = get_history(ticker, period=period, outputsize="full")
            if hist is None or hist.empty:
                excluded.append(ticker)
                continue
            close = hist["Close"].astype(float)
            close.index = pd.to_datetime(close.index)
            ret = close.pct_change().dropna()
            if len(ret) < 30:
                excluded.append(ticker)
                continue
            series_map[ticker] = ret
        except Exception:
            excluded.append(ticker)

    if len(series_map) < 2:
        return CorrelationMatrix(
            excluded=excluded,
            error="Fewer than 2 tickers had sufficient history",
        )

    returns_df = pd.DataFrame(series_map)
    corr = returns_df.corr().round(2)
    valid_tickers = list(corr.columns)
    matrix = [[float(corr.loc[r, c]) for c in valid_tickers] for r in valid_tickers]

    dates = returns_df.index
    dates_range = f"{str(dates.min())[:10]} to {str(dates.max())[:10]}"

    return CorrelationMatrix(
        tickers=valid_tickers,
        matrix=matrix,
        dates_range=dates_range,
        excluded=excluded,
    )
