"""Spot-price backfill for tokenized metals (XAU/XAG).

CoinGlass has no spot market for these commodity symbols, so the daily
``spot_price_d1`` series is composed from two public sources:

- LBMA daily fixings (USD/oz, business days) — everything before the perp
  listing on Bybit. The boundary is auto-detected from the earliest Bybit
  kline, never hardcoded;
- Bybit USDT-perp daily klines from the listing on (the perp tracks spot
  closely); volume is the USD turnover, so the still-forming last candle is
  refreshed on every run.

Each source fails independently; LBMA rows only fill gaps and never replace
already-stored points (perp candles win on their days).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from . import store

LBMA_URLS = {
    "XAU": "https://prices.lbma.org.uk/json/gold_pm.json",
    "XAG": "https://prices.lbma.org.uk/json/silver.json",
}
BYBIT_PERP = {"XAU": "XAUUSDT", "XAG": "XAGUSDT"}
BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BACKFILL_FROM = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
DAY_MS = 86_400_000
HTTP_TIMEOUT = 60.0


def _get_json(url: str, params: dict | None = None):
    resp = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _lbma_rows(url: str) -> list[tuple[int, dict]]:
    rows = []
    for rec in _get_json(url):
        price = (rec.get("v") or [None] * 3)[0]
        if price is None:
            continue
        ts = int(datetime.fromisoformat(rec["d"]).replace(tzinfo=timezone.utc).timestamp())
        rows.append((ts, {"open": price, "high": price, "low": price,
                          "close": price, "volume": None, "src": "lbma"}))
    return rows


def _bybit_rows(symbol: str) -> list[tuple[int, dict]]:
    inst = _get_json(BYBIT_INSTRUMENTS_URL, {"category": "linear", "symbol": symbol})
    lst = (inst.get("result") or {}).get("list") or []
    if not lst or not lst[0].get("launchTime"):
        return []
    start = int(lst[0]["launchTime"])
    now_ms = int(time.time() * 1000)
    rows: dict[int, dict] = {}
    while start <= now_ms:
        end = min(start + 999 * DAY_MS, now_ms)
        data = _get_json(BYBIT_KLINE_URL, {
            "category": "linear", "symbol": symbol, "interval": "D",
            "start": start, "end": end, "limit": 1000})
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit kline: {data.get('retMsg')}")
        for k in (data.get("result") or {}).get("list") or []:
            rows[int(k[0]) // 1000] = {
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[6]) if len(k) > 6 and k[6] else float(k[5]),
                "src": "perp"}
        start = end + 1
    return sorted(rows.items())


def backfill_spot(symbol: str) -> dict[str, int]:
    """Upsert the composite ``spot_price_d1`` series for XAU/XAG."""
    symbol = symbol.upper()
    if symbol not in LBMA_URLS:
        return {}
    counts: dict[str, int] = {}
    perp_rows: list[tuple[int, dict]] = []
    try:
        perp_rows = _bybit_rows(BYBIT_PERP[symbol])
    except Exception:  # noqa: BLE001 — sources fail independently
        pass
    store.upsert_points(symbol, "spot_price_d1", perp_rows)
    counts["spot_perp"] = len(perp_rows)

    boundary = min((ts for ts, _ in perp_rows), default=None)
    existing: set[int] = set()
    for ts, vals in store.get_points(symbol, "spot_price_d1"):
        existing.add(ts)
        if vals.get("src") == "perp" and (boundary is None or ts < boundary):
            boundary = ts
    try:
        rows = [r for r in _lbma_rows(LBMA_URLS[symbol])
                if r[0] >= BACKFILL_FROM and r[0] not in existing
                and (boundary is None or r[0] < boundary)]
    except Exception:  # noqa: BLE001
        rows = []
    store.upsert_points(symbol, "spot_price_d1", rows)
    counts["spot_lbma"] = len(rows)
    return counts
