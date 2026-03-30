"""
Database connection and initialization for NANTUCKET.

All data lives in ~/.nantucket/nantucket.db — a single SQLite file in your
home directory. This means:
- No setup required
- Works offline
- Easy to back up (just copy the file)
- Easy to reset (just delete the file)
"""

import sqlite3
from pathlib import Path

from nantucket.db.models import ALL_TABLES

# Where we store the database file
DEFAULT_DB_PATH = Path.home() / ".nantucket" / "nantucket.db"


def get_db_path() -> Path:
    """Return the database file path, creating the parent directory if needed."""
    db_path = DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection() -> sqlite3.Connection:
    """
    Open a SQLite connection with useful defaults:

    - row_factory = sqlite3.Row  →  lets you access columns by name like a dict
      (e.g. row['ticker'] instead of row[0])
    - WAL journal mode  →  better performance when reading and writing at the
      same time (not critical for personal use, but a good habit)
    - Foreign keys ON  →  enforces referential integrity if we add FKs later
    """
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """
    Create all tables if they don't exist yet.
    Called once at startup from cli.py so every command has a fresh DB.
    """
    with get_connection() as conn:
        for create_sql in ALL_TABLES:
            conn.execute(create_sql)
        conn.commit()
