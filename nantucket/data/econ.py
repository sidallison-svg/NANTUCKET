"""
Economic calendar via the FRED (Federal Reserve Economic Data) API.

Free API key available at https://fred.stlouisfed.org/docs/api/api_key.html
Set FRED_API_KEY environment variable to enable this feature.

Without a key, the module returns gracefully with an explanation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import requests

FRED_BASE = "https://api.stlouisfed.org/fred"
_CACHE_KEY = "econ_calendar"
_CACHE_TTL = 360  # 6 hours

# Curated list of high-importance macro indicators
WATCHED_SERIES: list[dict] = [
    {"name": "CPI (YoY)",          "series_id": "CPIAUCSL",        "importance": "high"},
    {"name": "Core CPI",           "series_id": "CPILFESL",        "importance": "high"},
    {"name": "Non-Farm Payrolls",  "series_id": "PAYEMS",          "importance": "high"},
    {"name": "Unemployment Rate",  "series_id": "UNRATE",          "importance": "high"},
    {"name": "GDP Growth Rate",    "series_id": "A191RL1Q225SBEA", "importance": "high"},
    {"name": "Fed Funds Rate",     "series_id": "FEDFUNDS",        "importance": "high"},
    {"name": "PCE Inflation",      "series_id": "PCEPI",           "importance": "high"},
    {"name": "Retail Sales",       "series_id": "RSXFS",           "importance": "medium"},
    {"name": "10Y Treasury Yield", "series_id": "DGS10",           "importance": "medium"},
    {"name": "ISM Manufacturing",  "series_id": "MANEMP",          "importance": "medium"},
]


@dataclass
class EconEvent:
    name: str
    series_id: str
    release_date: str       # "2026-04-10"
    importance: str         # "high" | "medium" | "low"
    previous: Optional[float] = None
    actual: Optional[float] = None
    days_away: int = 0


@dataclass
class EconCalendar:
    events: list[EconEvent] = field(default_factory=list)
    fetched_at: str = ""
    error: Optional[str] = None


def get_econ_calendar(days_ahead: int = 45) -> EconCalendar:
    """Fetch upcoming economic calendar events from FRED."""
    from nantucket.db.database import get_econ_cached, set_econ_cache

    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return EconCalendar(
            fetched_at=datetime.now().isoformat(),
            error="Set FRED_API_KEY environment variable for economic calendar data. "
                  "Free key at fred.stlouisfed.org",
        )

    cached = get_econ_cached(_CACHE_KEY, _CACHE_TTL)
    if cached:
        events = [EconEvent(**e) for e in cached["events"]]
        return EconCalendar(events=events, fetched_at=cached["fetched_at"])

    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)
    events: list[EconEvent] = []

    for series in WATCHED_SERIES:
        try:
            # Get vintage (release) dates
            resp = requests.get(
                f"{FRED_BASE}/series/vintagedates",
                params={"series_id": series["series_id"], "api_key": api_key, "file_type": "json"},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            dates = resp.json().get("vintage_dates", [])

            # Find the next upcoming release date
            upcoming = [d for d in dates if datetime.strptime(d, "%Y-%m-%d").date() >= today]
            if not upcoming:
                continue
            release_date_str = min(upcoming)
            release_date = datetime.strptime(release_date_str, "%Y-%m-%d").date()
            if release_date > cutoff:
                continue

            days_away = (release_date - today).days

            # Get the most recent actual value
            obs_resp = requests.get(
                f"{FRED_BASE}/series/observations",
                params={
                    "series_id": series["series_id"],
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 2,
                },
                timeout=10,
            )
            previous: Optional[float] = None
            actual: Optional[float] = None
            if obs_resp.status_code == 200:
                obs_list = obs_resp.json().get("observations", [])
                for i, obs in enumerate(obs_list):
                    val = obs.get("value", ".")
                    if val != ".":
                        try:
                            parsed = round(float(val), 2)
                            if i == 0:
                                actual = parsed
                            else:
                                previous = parsed
                        except ValueError:
                            pass

            events.append(EconEvent(
                name=series["name"],
                series_id=series["series_id"],
                release_date=release_date_str,
                importance=series["importance"],
                previous=previous,
                actual=actual,
                days_away=days_away,
            ))

        except Exception:
            continue

    events.sort(key=lambda e: e.release_date)
    fetched_at = datetime.now().isoformat()

    set_econ_cache(_CACHE_KEY, {
        "events": [e.__dict__ for e in events],
        "fetched_at": fetched_at,
    })

    return EconCalendar(events=events, fetched_at=fetched_at)
