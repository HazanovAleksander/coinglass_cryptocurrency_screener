"""SQLite cache for CoinGlass dashboard series.

All fetched data is cached in a local SQLite database (folder ``data/sqlite``
by default, override with env ``CG_DB_DIR``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()

SERIES_NAMES = [
    "funding",
    "oi_agg",
    "oi_exchange_h1",
    "oi_exchange_d1",
    "fut_buysell",
    "spot_buysell",
    "spot_price",
]

# The dashboard is daily-only. Only d1 series are stored/read; the ``timeframe``
# args that remain in some signatures are accepted for compatibility but d1 is
# the only meaningful value.
TF_SERIES = {
    "d1": {
        "funding_d1", "oi_agg_d1", "oi_exchange_h1_d1",
        "oi_exchange_d1_d1", "fut_buysell_d1",
        "spot_buysell_d1", "spot_price_d1",
    },
}


def resolve_series(name: str, timeframe: str = "d1") -> str:
    """Resolve a base (or already-suffixed) series name to its stored d1 name."""
    wanted = TF_SERIES["d1"]
    if name in wanted:
        return name
    candidate = f"{name}_d1"
    if candidate in wanted:
        return candidate
    raise ValueError(f"unknown series: {name}")


def available_series_for(symbol: str, timeframe: str = "d1") -> list[str]:
    """Return cached d1 series names for the symbol."""
    wanted = TF_SERIES["d1"]
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT series FROM series_points WHERE symbol=? AND series in ({})".format(
                ",".join("?" * len(wanted))
            ),
            (symbol, *sorted(wanted)),
        ).fetchall()
    return [r[0] for r in rows]


def db_dir() -> Path:
    return Path(os.environ.get("CG_DB_DIR", "data/sqlite"))


def db_path() -> Path:
    return db_dir() / "coinglass_cache.db"


def _connect() -> sqlite3.Connection:
    db_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS series_points (
                symbol TEXT NOT NULL,
                series TEXT NOT NULL,
                ts INTEGER NOT NULL,
                vals TEXT NOT NULL,
                PRIMARY KEY (symbol, series, ts)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )


def upsert_points(symbol: str, series: str, rows: list[tuple[int, dict]]) -> int:
    """rows: [(ts_seconds, values_dict), ...]"""
    if not rows:
        return 0
    with _LOCK, _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO series_points (symbol, series, ts, vals)"
            " VALUES (?, ?, ?, ?)",
            [(symbol, series, int(ts), json.dumps(vals)) for ts, vals in rows],
        )
        conn.commit()
    return len(rows)


def get_points(
    symbol: str,
    series: str,
    limit: int | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> list[tuple[int, dict]]:
    """Return cached points for a series in ascending ts order.

    Optional ``from_ts``/``to_ts`` (epoch seconds, inclusive) restrict the
    window; ``limit`` caps the number of returned points (most recent within
    the window when a range is given).
    """
    where = ["symbol=?", "series=?"]
    params: list = [symbol, series]
    if from_ts is not None:
        where.append("ts >= ?")
        params.append(int(from_ts))
    if to_ts is not None:
        where.append("ts <= ?")
        params.append(int(to_ts))
    with _LOCK, _connect() as conn:
        q = (
            "SELECT ts, vals FROM series_points WHERE "
            + " AND ".join(where)
            + " ORDER BY ts ASC"
        )
        rows = conn.execute(q, params).fetchall()
    if limit and len(rows) > limit:
        rows = rows[-int(limit):]
    return [(ts, json.loads(v)) for ts, v in rows]


def set_meta(key: str, value) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), int(time.time())),
        )
        conn.commit()


def get_meta(key: str, max_age: int | None = None):
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT value, updated_at FROM meta WHERE key=?", (key,)
        ).fetchone()
    if not row:
        return None
    value, updated_at = row
    if max_age is not None and time.time() - updated_at > max_age:
        return None
    return json.loads(value)


def is_fresh(symbol: str, stale_days: int = 1) -> bool:
    """True when the newest cached point of the key d1 series is fresher than
    ``stale_days`` days.

    Cached history is immutable, so the only reason to re-scrape is to pull
    newly-published days. That is decided solely by the right edge of the data
    itself (not by fetch time / TTL). The short ``oi_exchange_h1_d1`` fallback
    is excluded (it only spans a few days); the left edge / inception gap and
    any historical holes are ignored.
    """
    threshold = int(time.time()) - int(stale_days) * 86400
    key = sorted(TF_SERIES["d1"] - {"oi_exchange_h1_d1"})
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT MAX(ts) FROM series_points WHERE symbol=? AND series IN ({})".format(
                ",".join("?" * len(key))
            ),
            (symbol, *key),
        ).fetchone()
    return row is not None and row[0] is not None and row[0] >= threshold


def symbol_stats(symbol: str) -> dict:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT series, COUNT(*), MIN(ts), MAX(ts) FROM series_points"
            " WHERE symbol=? GROUP BY series",
            (symbol,),
        ).fetchall()
    return {r[0]: {"count": r[1], "min_ts": r[2], "max_ts": r[3]} for r in rows}
