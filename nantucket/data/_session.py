"""
Shared requests session for yfinance calls.

Yahoo Finance blocks curl_cffi on some systems (macOS SSL error 35).
Passing a standard requests.Session() bypasses curl_cffi entirely and
uses Python's built-in SSL stack, which works reliably everywhere.
"""

import requests

_session: requests.Session | None = None


def get_session() -> requests.Session:
    """Return a shared requests session configured for Yahoo Finance."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
    return _session
