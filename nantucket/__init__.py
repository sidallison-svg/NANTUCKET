"""
NANTUCKET — Personal investment screener, portfolio tracker, and market learning tool.

Phase 1: CLI screener + watchlist + Rich tables
Phase 2: Paper trading portfolio with P&L tracking
Phase 3: Claude AI integration (explain + bull-bear)
Phase 4: Web dashboard with charts
Phase 5: Crypto (CoinGecko) + Futures (yfinance) data sources
"""

import os
from pathlib import Path

# Auto-load .env file from project root if it exists
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

__version__ = "0.1.0"
__author__ = "NANTUCKET"
