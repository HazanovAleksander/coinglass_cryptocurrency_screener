"""Fetch CoinGlass data via the Node/puppeteer scraper and cache into SQLite.

The scraper prints ``PROGRESS <i> <n> <stage>`` lines on stderr; we surface
those through an in-memory job registry that the frontend polls to render a
status bar.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import backfill, store

SCRAPER_DIR = Path(os.environ.get("CG_SCRAPER_DIR", Path(__file__).resolve().parent.parent / "scraper"))
NODE_BIN = os.environ.get("CG_NODE_BIN", "node")
FETCH_LIMIT = int(os.environ.get("CG_FETCH_LIMIT", "4500"))
MAX_FETCH_LIMIT = int(os.environ.get("CG_MAX_FETCH_LIMIT", "4500"))
STALE_DAYS = int(os.environ.get("CG_STALE_DAYS", "1"))
TOP20_TTL = int(os.environ.get("CG_TOP20_TTL", "86400"))

FALLBACK_TOP20 = [
    "BTC", "ETH", "XRP", "BNB", "SOL", "DOGE", "TRX", "ADA",
    "LINK", "XLM", "SUI", "AVAX", "LTC", "TON", "SHIB", "HBAR",
    "BCH", "DOT", "UNI", "PEPE",
]

EXCLUDE_SYMBOLS = {"WBTC"}
STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "PYUSD",
    "USDS", "USDE", "USD1", "FRAX", "USDD",
}
EXTRA_SYMBOLS = ["PEPE", "XAU", "XAG"]
EXTRA_NAMES = {"XAU": "Gold", "XAG": "Silver"}

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------- job registry

def _job_new(symbol: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "symbol": symbol,
            "state": "running",
            "stage": "queued",
            "current": 0,
            "total": 1,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
    return job_id


def _job_update(job_id: str, **kw) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def job_status(job_id: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def running_job_for(symbol: str) -> dict | None:
    with _jobs_lock:
        for j in _jobs.values():
            if j["symbol"] == symbol and j["state"] == "running":
                return dict(j)
    return None


# ---------------------------------------------------------------- scraping

def _run_scraper(symbol: str, limit: int, job_id: str, timeframe: str = "d1",
                 on_progress=None) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = tmp.name
    try:
        proc = subprocess.Popen(
            [NODE_BIN, str(SCRAPER_DIR / "fetch_bundle.js"), symbol, str(limit), out_path, timeframe],
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            cwd=str(SCRAPER_DIR),
        )
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if line.startswith("PROGRESS "):
                parts = line.split(" ", 3)
                if len(parts) == 4:
                    _job_update(
                        job_id,
                        current=int(parts[1]),
                        total=int(parts[2]),
                        stage=parts[3],
                    )
                    if on_progress:
                        on_progress(int(parts[1]), int(parts[2]), parts[3])
            elif line.startswith("ERROR"):
                _job_update(job_id, error=line)
        rc = proc.wait(timeout=600)
        if rc != 0:
            raise RuntimeError(f"scraper exited rc={rc}")
        with open(out_path) as fh:
            return json.load(fh)
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


# ---------------------------------------------------------------- normalization

def _num(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _normalize_bundle(symbol: str, bundle: dict, timeframe: str = "d1") -> dict[str, int]:
    """Convert scraper bundle into per-series (ts, values) rows and upsert.

    The dashboard is daily-only; series are stored with the ``_d1`` suffix.
    ``spot_price`` is always aggregated into 24h buckets because coins without
    a Binance USDT pair fall back to hourly klines in the scraper.
    """
    series = bundle.get("series", {})
    counts: dict[str, int] = {}
    tf = "_d1"

    def payload(name):
        obj = series.get(name)
        if not isinstance(obj, dict) or obj.get("__err"):
            return None
        return obj.get("data")

    # funding: [{data:[ts,o,h,l,c], price:[po,pc]}]
    d = payload("funding")
    if isinstance(d, list):
        rows = []
        for r in d:
            k = r.get("data") or []
            p = r.get("price") or []
            if len(k) >= 5:
                rows.append((int(k[0]), {
                    "open": _num(k[1]), "high": _num(k[2]),
                    "low": _num(k[3]), "close": _num(k[4]),
                    "price_open": _num(p[0]) if len(p) > 0 else None,
                    "price_close": _num(p[1]) if len(p) > 1 else None,
                }))
        counts[f"funding{tf}"] = store.upsert_points(symbol, f"funding{tf}", rows)

    # oi_agg: [[ts,o,h,l,c]]
    d = payload("oi_agg")
    if isinstance(d, list):
        rows = [
            (int(r[0]), {"open": _num(r[1]), "high": _num(r[2]),
                         "low": _num(r[3]), "close": _num(r[4])})
            for r in d if isinstance(r, list) and len(r) >= 5
        ]
        counts[f"oi_agg{tf}"] = store.upsert_points(symbol, f"oi_agg{tf}", rows)

    # OI by exchange: {dataMap:{ex:[...]}, dateList:[ms], priceList:[...]}
    for src, dst in (("oi_by_exchange_hourly", f"oi_exchange_h1{tf}"),
                     ("oi_by_exchange_daily", f"oi_exchange_d1{tf}")):
        d = payload(src)
        if isinstance(d, dict) and d.get("dateList"):
            dates = d["dateList"]
            data_map = d.get("dataMap") or {}
            prices = d.get("priceList") or []
            rows = []
            for i, ms in enumerate(dates):
                vals = {ex: _num(arr[i]) for ex, arr in data_map.items()
                        if isinstance(arr, list) and i < len(arr)}
                if i < len(prices):
                    vals["_price"] = _num(prices[i])
                rows.append((int(ms // 1000), vals))
            counts[dst] = store.upsert_points(symbol, dst, rows)

    # buy/sell volumes: [[ts, buy, sell]]
    for src, dst in (("fut_buysell", f"fut_buysell{tf}"), ("spot_buysell", f"spot_buysell{tf}")):
        d = payload(src)
        if isinstance(d, list):
            rows = [
                (int(r[0]), {"buy": _num(r[1]), "sell": _num(r[2])})
                for r in d if isinstance(r, list) and len(r) >= 3
            ]
            counts[dst] = store.upsert_points(symbol, dst, rows)

    # spot price: [[ts,o,h,l,c,vol]] — aggregate into daily buckets (scraper
    # returns daily klines for coins with a Binance pair, hourly otherwise).
    d = payload("spot_price")
    if isinstance(d, list):
        rows = [
            (int(r[0]), {"open": _num(r[1]), "high": _num(r[2]), "low": _num(r[3]),
                         "close": _num(r[4]), "volume": _num(r[5]) if len(r) > 5 else None})
            for r in d if isinstance(r, list) and len(r) >= 5
        ]
        if rows:
            rows = _aggregate_ohlcv(rows, 24 * 3600)
        counts[f"spot_price{tf}"] = store.upsert_points(symbol, f"spot_price{tf}", rows)

    return counts


# ---------------------------------------------------------------- aggregation helpers

def _aggregate_ohlcv(rows, bucket_secs):
    """Aggregate hourly OHLCV rows into larger buckets (4h / 24h)."""
    rows = sorted(rows, key=lambda r: r[0])
    out = []
    last_key = None
    cur = None
    for ts, v in rows:
        key = ts - (ts % bucket_secs)
        if key != last_key:
            if cur is not None:
                out.append(cur)
            cur = (key, {"open": v["open"], "high": v["high"], "low": v["low"],
                         "close": v["close"], "volume": v.get("volume") or 0.0})
            last_key = key
        else:
            cur[1]["high"] = v["high"] if cur[1]["high"] is None else max(cur[1]["high"], v["high"])
            cur[1]["low"] = v["low"] if cur[1]["low"] is None else min(cur[1]["low"], v["low"])
            cur[1]["close"] = v["close"]
            cur[1]["volume"] = (cur[1]["volume"] or 0) + (v.get("volume") or 0)
    if cur is not None:
        out.append(cur)
    return out


# ---------------------------------------------------------------- public API

def refresh_symbol_async(symbol: str, force: bool = False, timeframe: str = "d1",
                         from_ts: int | None = None, to_ts: int | None = None) -> dict:
    """Kick off a background fetch for a symbol. Returns job descriptor.

    The dashboard is daily-only; ``timeframe`` is accepted for compatibility.
    Cached history is immutable, so a scrape is only needed when there are
    newly-published days to pull. That is decided by the right edge of the
    data itself (``store.is_fresh``): if the newest point of the key d1 series
    is within ``STALE_DAYS`` of now, a ``fresh`` job is returned and the scrape
    is skipped. ``force=True`` bypasses this check (escape hatch). ``from_ts``
    /``to_ts`` are accepted for compatibility and do not affect the decision.
    """
    symbol = symbol.upper()
    timeframe = "d1"
    running = running_job_for(symbol)
    if running:
        return running

    fresh = {"id": None, "symbol": symbol, "state": "fresh",
             "stage": "cached", "current": 1, "total": 1, "error": None}
    if not force and store.is_fresh(symbol, STALE_DAYS):
        return fresh
    job_id = _job_new(symbol)

    def worker():
        try:
            bundle = _run_scraper(symbol, FETCH_LIMIT, job_id, timeframe)
            counts = _normalize_bundle(symbol, bundle, timeframe)
            _job_update(job_id, stage="spot_backfill", current=1, total=2)
            counts.update(backfill.backfill_spot(symbol))
            store.set_meta(f"last_fetch:{symbol}", {
                "ts": int(time.time()), "counts": counts,
            })
            _job_update(job_id, state="done", stage="done",
                        finished_at=time.time())
        except Exception as exc:  # noqa: BLE001
            _job_update(job_id, state="error", error=str(exc),
                        finished_at=time.time())

    threading.Thread(target=worker, daemon=True).start()
    return job_status(job_id) or {}


def refresh_symbol_sync(symbol: str, force: bool = False,
                        on_progress=None) -> dict:
    """Blocking fetch for CLI usage; mirrors ``refresh_symbol_async``.

    Same freshness short-circuit (``store.is_fresh`` unless ``force``), but the
    scrape runs in the foreground and each ``PROGRESS`` line fires
    ``on_progress(current, total, stage)``. Returns a job-like descriptor with
    ``counts`` attached; ``state`` is ``fresh|done|error``.
    """
    symbol = symbol.upper()
    if not force and store.is_fresh(symbol, STALE_DAYS):
        return {"id": None, "symbol": symbol, "state": "fresh",
                "stage": "cached", "current": 1, "total": 1, "error": None,
                "counts": None}
    job_id = _job_new(symbol)
    try:
        bundle = _run_scraper(symbol, FETCH_LIMIT, job_id, "d1", on_progress)
        counts = _normalize_bundle(symbol, bundle, "d1")
        _job_update(job_id, stage="spot_backfill", current=1, total=2)
        counts.update(backfill.backfill_spot(symbol))
        store.set_meta(f"last_fetch:{symbol}",
                       {"ts": int(time.time()), "counts": counts})
        _job_update(job_id, state="done", stage="done",
                    finished_at=time.time())
        return {"id": job_id, "symbol": symbol, "state": "done",
                "stage": "done", "current": 1, "total": 1, "error": None,
                "counts": counts}
    except Exception as exc:  # noqa: BLE001
        _job_update(job_id, state="error", error=str(exc),
                    finished_at=time.time())
        return {"id": job_id, "symbol": symbol, "state": "error",
                "stage": "error", "current": 1, "total": 1, "error": str(exc),
                "counts": None}


def _filter_top20(coins: list[dict], limit: int | None = None) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for c in coins:
        sym = (c.get("symbol") or "").upper()
        if not sym or sym in seen or sym in EXCLUDE_SYMBOLS or sym in STABLECOIN_SYMBOLS:
            continue
        seen.add(sym)
        out.append(c)
        if limit and len(out) >= limit:
            break
    for sym in EXTRA_SYMBOLS:
        if sym not in seen:
            out.append({"symbol": sym, "name": EXTRA_NAMES.get(sym, sym),
                        "market_cap": None, "price": None})
    return out


def get_top20(force: bool = False) -> list[dict]:
    """Top-20 coins by market cap (cached daily), minus wrapped-BTC/stablecoin
    entries, plus EXTRA_SYMBOLS. Falls back to static list."""
    cached = None if force else store.get_meta("top20", max_age=TOP20_TTL)
    if cached:
        return _filter_top20(cached)
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            out_path = tmp.name
        proc = subprocess.run(
            [NODE_BIN, str(SCRAPER_DIR / "fetch_bundle.js"), "_TOP20", "25", out_path],
            capture_output=True, text=True, timeout=180, cwd=str(SCRAPER_DIR),
        )
        if proc.returncode == 0:
            with open(out_path) as fh:
                bundle = json.load(fh)
            data = (bundle.get("series", {}).get("top20") or {}).get("data") or {}
            lst = data.get("list") or []
            coins = [
                {"symbol": c.get("symbol"), "name": c.get("coinName") or c.get("symbol"),
                 "market_cap": c.get("marketCap"), "price": c.get("price")}
                for c in lst if c.get("symbol")
            ]
            coins = _filter_top20(coins, limit=20)
            if coins:
                store.set_meta("top20", coins)
                return coins
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    return _filter_top20(
        [{"symbol": s, "name": s, "market_cap": None, "price": None}
         for s in FALLBACK_TOP20], limit=20)
