"""
CoinGecko API wrapper for cryptocurrency data.

CoinGecko offers a free public API (no API key required for basic use) with:
- Real-time prices in USD
- Market cap, volume, 24h/7d price changes
- Top coins by market cap

Rate limits (free tier): ~10-30 requests/minute
Docs: https://www.coingecko.com/api/documentation

Why CoinGecko instead of yfinance for crypto?
yfinance does have some crypto support (BTC-USD, ETH-USD) but it's unreliable
and missing many coins. CoinGecko is purpose-built for crypto data.

Ticker ↔ CoinGecko ID mapping:
CoinGecko uses IDs like "bitcoin" not "BTC". We maintain a mapping table
for the most common coins and fall back to a search API for unknown tickers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Mapping from common ticker symbols to CoinGecko coin IDs
TICKER_TO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "USDC": "usd-coin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "SHIB": "shiba-inu",
    "MATIC": "matic-network",
    "LTC": "litecoin",
    "UNI": "uniswap",
    "LINK": "chainlink",
    "ATOM": "cosmos",
    "XLM": "stellar",
    "NEAR": "near",
    "ALGO": "algorand",
    "ICP": "internet-computer",
    "FIL": "filecoin",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "TON": "the-open-network",
}


@dataclass
class CryptoQuote:
    """Current price and market data for a cryptocurrency."""
    ticker: str           # e.g. "BTC"
    coin_id: str          # CoinGecko ID e.g. "bitcoin"
    name: str = ""
    price: float = 0.0
    change_pct: float = 0.0       # 24h change
    change_7d_pct: float = 0.0    # 7-day change
    volume_24h: float = 0.0       # 24h trading volume in USD
    market_cap: float = 0.0
    market_cap_rank: Optional[int] = None
    ath: float = 0.0              # All-time high price
    ath_change_pct: float = 0.0   # % below all-time high
    asset_type: str = "crypto"
    error: Optional[str] = None


def ticker_to_coin_id(ticker: str) -> Optional[str]:
    """
    Convert a ticker symbol to a CoinGecko coin ID.
    Returns None if the ticker isn't in our mapping.
    """
    return TICKER_TO_ID.get(ticker.upper())


def get_crypto_quote(ticker: str) -> CryptoQuote:
    """
    Fetch current data for a single cryptocurrency by ticker symbol.
    Returns a CryptoQuote with error set if data can't be fetched.
    """
    ticker = ticker.upper()
    coin_id = ticker_to_coin_id(ticker)

    if not coin_id:
        return CryptoQuote(
            ticker=ticker,
            coin_id="",
            error=f"Unknown crypto ticker '{ticker}'. Add it to TICKER_TO_ID in crypto.py",
        )

    return get_crypto_by_id(ticker, coin_id)


def get_crypto_by_id(ticker: str, coin_id: str) -> CryptoQuote:
    """Fetch crypto data using the CoinGecko coin ID directly."""
    try:
        url = f"{COINGECKO_BASE}/coins/markets"
        params = {
            "vs_currency": "usd",
            "ids": coin_id,
            "price_change_percentage": "24h,7d",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return CryptoQuote(
                ticker=ticker, coin_id=coin_id,
                error=f"No data returned for coin ID '{coin_id}'"
            )

        c = data[0]
        return CryptoQuote(
            ticker=ticker,
            coin_id=coin_id,
            name=c.get("name", ticker),
            price=c.get("current_price") or 0.0,
            change_pct=c.get("price_change_percentage_24h") or 0.0,
            change_7d_pct=c.get("price_change_percentage_7d_in_currency") or 0.0,
            volume_24h=c.get("total_volume") or 0.0,
            market_cap=c.get("market_cap") or 0.0,
            market_cap_rank=c.get("market_cap_rank"),
            ath=c.get("ath") or 0.0,
            ath_change_pct=c.get("ath_change_percentage") or 0.0,
        )

    except requests.exceptions.RequestException as exc:
        return CryptoQuote(ticker=ticker, coin_id=coin_id, error=str(exc))


def get_top_crypto(limit: int = 20) -> list[CryptoQuote]:
    """
    Fetch the top N cryptocurrencies by market cap.
    Useful for the screener's crypto universe.
    """
    try:
        url = f"{COINGECKO_BASE}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "price_change_percentage": "24h,7d",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        coins = resp.json()

        quotes = []
        # Build reverse map: coin_id → ticker
        id_to_ticker = {v: k for k, v in TICKER_TO_ID.items()}

        for c in coins:
            coin_id = c.get("id", "")
            ticker = id_to_ticker.get(coin_id, c.get("symbol", "").upper())
            quotes.append(CryptoQuote(
                ticker=ticker,
                coin_id=coin_id,
                name=c.get("name", ticker),
                price=c.get("current_price") or 0.0,
                change_pct=c.get("price_change_percentage_24h") or 0.0,
                change_7d_pct=c.get("price_change_percentage_7d_in_currency") or 0.0,
                volume_24h=c.get("total_volume") or 0.0,
                market_cap=c.get("market_cap") or 0.0,
                market_cap_rank=c.get("market_cap_rank"),
                ath=c.get("ath") or 0.0,
                ath_change_pct=c.get("ath_change_percentage") or 0.0,
            ))
        return quotes

    except Exception as exc:
        return []
