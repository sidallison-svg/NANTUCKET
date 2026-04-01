"""
Technical indicators computed from get_history() DataFrames.

All math uses only pandas + numpy — no TA-Lib or pandas_ta required.

Implementations match standard charting platform formulas:
- RSI:  Wilder's smoothing (ewm com=period-1) — matches TradingView
- MACD: Standard 12/26/9 EMA (adjust=False) — recursive EMA
- BB:   20-period SMA ± 2 × population std (ddof=0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class RSIResult:
    values: list[float]
    dates: list[str]
    current: float
    overbought: bool    # RSI > 70
    oversold: bool      # RSI < 30


@dataclass
class MACDResult:
    macd_line: list[float]
    signal_line: list[float]
    histogram: list[float]
    dates: list[str]
    current_macd: float
    current_signal: float
    bullish_crossover: bool     # MACD crossed above signal on last bar


@dataclass
class BollingerResult:
    upper: list[float]
    middle: list[float]
    lower: list[float]
    dates: list[str]
    current_price: float
    pct_b: float        # (price - lower) / (upper - lower)
    squeeze: bool       # bandwidth below 20-period average → volatility contraction


@dataclass
class TechnicalIndicators:
    ticker: str
    rsi: Optional[RSIResult] = None
    macd: Optional[MACDResult] = None
    bollinger: Optional[BollingerResult] = None
    error: Optional[str] = None


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).round(2)


def _compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = (ema_fast - ema_slow).round(4)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean().round(4)
    histogram = (macd_line - signal_line).round(4)
    return macd_line, signal_line, histogram


def _compute_bollinger(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    sma = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = (sma + num_std * std).round(4)
    lower = (sma - num_std * std).round(4)
    return upper, sma.round(4), lower


def _series_tail(s: pd.Series, n: int = 100) -> tuple[list[float], list[str]]:
    """Return last n non-NaN values and their ISO date strings."""
    s = s.dropna().iloc[-n:]
    vals = [round(float(v), 4) for v in s.values]
    dates = [str(d)[:10] for d in s.index]
    return vals, dates


def get_indicators(ticker: str, period: str = "1y") -> TechnicalIndicators:
    """
    Compute RSI, MACD, and Bollinger Bands for a ticker.
    Relies on the existing get_history() 24-hour cache — no extra caching needed.
    """
    from nantucket.data.stocks import get_history

    try:
        hist = get_history(ticker, period=period, outputsize="full")
        if hist is None or hist.empty or len(hist) < 30:
            return TechnicalIndicators(ticker=ticker, error="Insufficient price history")

        close = hist["Close"].astype(float)

        # RSI
        rsi_series = _compute_rsi(close)
        rsi_vals, rsi_dates = _series_tail(rsi_series, 100)
        current_rsi = rsi_vals[-1] if rsi_vals else float("nan")
        rsi_result = RSIResult(
            values=rsi_vals,
            dates=rsi_dates,
            current=round(current_rsi, 2),
            overbought=current_rsi > 70,
            oversold=current_rsi < 30,
        )

        # MACD
        macd_line, signal_line, histogram = _compute_macd(close)
        m_vals, m_dates = _series_tail(macd_line, 100)
        s_vals, _ = _series_tail(signal_line, 100)
        h_vals, _ = _series_tail(histogram, 100)
        bullish_cross = (
            len(h_vals) >= 2
            and h_vals[-1] > 0
            and h_vals[-2] <= 0
        )
        macd_result = MACDResult(
            macd_line=m_vals,
            signal_line=s_vals,
            histogram=h_vals,
            dates=m_dates,
            current_macd=m_vals[-1] if m_vals else 0.0,
            current_signal=s_vals[-1] if s_vals else 0.0,
            bullish_crossover=bullish_cross,
        )

        # Bollinger Bands
        bb_upper, bb_mid, bb_lower = _compute_bollinger(close)
        u_vals, bb_dates = _series_tail(bb_upper, 100)
        mid_vals, _ = _series_tail(bb_mid, 100)
        lo_vals, _ = _series_tail(bb_lower, 100)
        cur_price = float(close.iloc[-1])
        cur_upper = u_vals[-1] if u_vals else cur_price
        cur_lower = lo_vals[-1] if lo_vals else cur_price
        band_range = cur_upper - cur_lower
        pct_b = round((cur_price - cur_lower) / band_range, 3) if band_range > 0 else 0.5

        # Squeeze: current bandwidth vs 20-period avg bandwidth
        bw = (bb_upper - bb_lower) / bb_mid.replace(0, float("nan"))
        squeeze = bool(bw.iloc[-1] < bw.rolling(20).mean().iloc[-1]) if len(bw.dropna()) >= 20 else False

        bb_result = BollingerResult(
            upper=u_vals,
            middle=mid_vals,
            lower=lo_vals,
            dates=bb_dates,
            current_price=round(cur_price, 4),
            pct_b=pct_b,
            squeeze=squeeze,
        )

        return TechnicalIndicators(
            ticker=ticker,
            rsi=rsi_result,
            macd=macd_result,
            bollinger=bb_result,
        )

    except Exception as exc:
        return TechnicalIndicators(ticker=ticker, error=str(exc))
