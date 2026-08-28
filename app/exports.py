"""Export dashboard data into one merged table: CSV / XLSX / XLS / Google Sheets."""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone

from . import store

COLUMNS = [
    "timestamp",
    "datetime_utc",
    "spot_open", "spot_high", "spot_low", "spot_close", "spot_volume",
    "funding_close",
    "oi_agg_close",
    "oi_total_usd",
    "fut_vol_buy", "fut_vol_sell", "fut_vol_total", "fut_cvd",
    "spot_vol_buy", "spot_vol_sell", "spot_vol_total", "spot_cvd",
]

# Header-cell comments (Excel/Google "notes"): short description of each column.
COL_HELP = {
    "timestamp": "Временная метка (epoch, секунды UTC)",
    "datetime_utc": "Дата и время в UTC (YYYY-MM-DD HH:MM:SS)",
    "spot_open": "Цена открытия спот-свечи",
    "spot_high": "Максимальная цена спот-свечи",
    "spot_low": "Минимальная цена спот-свечи",
    "spot_close": "Цена закрытия спот-свечи",
    "spot_volume": "Объём спот-торгов за свечу",
    "funding_close": "Ставка финансирования (funding rate) на закрытии",
    "oi_agg_close": "Совокупный открытый интерес (агрегированный), цена закрытия",
    "oi_total_usd": "Суммарный открытый интерес по всем биржам, USD",
    "fut_vol_buy": "Объём покупок на фьючерсном рынке",
    "fut_vol_sell": "Объём продаж на фьючерсном рынке",
    "fut_vol_total": "Суммарный объём фьючерсных сделок (buy+sell)",
    "fut_cvd": "Кумулятивный объёмный дельта-профиль фьючерсов (накопленный buy−sell)",
    "spot_vol_buy": "Объём покупок на спотовом рынке",
    "spot_vol_sell": "Объём продаж на спотовом рынке",
    "spot_vol_total": "Суммарный объём спотовых сделок (buy+sell)",
    "spot_cvd": "Кумулятивный объёмный дельта-профиль спота (накопленный buy−sell)",
    "rv_7d": "Реализованная волатильность, скользящее окно 7 дней (годовая, %)",
}

# Dynamic columns (per-exchange OI) get a synthesized help text in col_help().
OI_EX_PREFIX = "oi_ex_"


def col_help(col: str) -> str | None:
    """Help text for an export column: static from COL_HELP, or synthesized
    for dynamic ``oi_ex_<exchange>`` columns."""
    help_text = COL_HELP.get(col)
    if help_text:
        return help_text
    if col.startswith(OI_EX_PREFIX):
        return f"Открытый интерес по бирже {col[len(OI_EX_PREFIX):]}, USD"
    return None


def build_table(symbol: str, limit: int | None = None, timeframe: str = "d1",
                from_ts: int | None = None, to_ts: int | None = None) -> list[dict]:
    """Merge all cached series for a symbol into one table.

    ``from_ts``/``to_ts`` (epoch seconds, inclusive) restrict the window.
    """
    symbol = symbol.upper()
    spot = dict(store.get_points(symbol, store.resolve_series("spot_price"), from_ts=from_ts, to_ts=to_ts))
    funding = dict(store.get_points(symbol, store.resolve_series("funding"), from_ts=from_ts, to_ts=to_ts))
    oi = dict(store.get_points(symbol, store.resolve_series("oi_agg"), from_ts=from_ts, to_ts=to_ts))
    oi_ex = dict(store.get_points(symbol, store.resolve_series("oi_exchange_h1"), from_ts=from_ts, to_ts=to_ts))
    fut_bs = dict(store.get_points(symbol, store.resolve_series("fut_buysell"), from_ts=from_ts, to_ts=to_ts))
    spot_bs = dict(store.get_points(symbol, store.resolve_series("spot_buysell"), from_ts=from_ts, to_ts=to_ts))

    ts_all = sorted(set(spot) | set(funding) | set(oi) | set(fut_bs) | set(spot_bs))
    if limit:
        ts_all = ts_all[-limit:]

    fut_cvd = 0.0
    spot_cvd = 0.0
    rows = []
    for ts in ts_all:
        s = spot.get(ts, {})
        f = funding.get(ts, {})
        o = oi.get(ts, {})
        fb = fut_bs.get(ts, {})
        sb = spot_bs.get(ts, {})
        ox = oi_ex.get(ts, {})

        fut_delta = None
        if fb.get("buy") is not None and fb.get("sell") is not None:
            fut_delta = fb["buy"] - fb["sell"]
            fut_cvd += fut_delta
        spot_delta = None
        if sb.get("buy") is not None and sb.get("sell") is not None:
            spot_delta = sb["buy"] - sb["sell"]
            spot_cvd += spot_delta

        oi_total = None
        ex_vals = [v for k, v in ox.items() if not k.startswith("_") and v is not None]
        if ex_vals:
            oi_total = sum(ex_vals)

        rows.append({
            "timestamp": ts,
            "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc)
                                    .strftime("%Y-%m-%d %H:%M:%S"),
            "spot_open": s.get("open"), "spot_high": s.get("high"),
            "spot_low": s.get("low"), "spot_close": s.get("close"),
            "spot_volume": s.get("volume"),
            "funding_close": f.get("close"),
            "oi_agg_close": o.get("close"),
            "oi_total_usd": oi_total,
            "fut_vol_buy": fb.get("buy"), "fut_vol_sell": fb.get("sell"),
            "fut_vol_total": (fb["buy"] + fb["sell"])
                if fb.get("buy") is not None and fb.get("sell") is not None else None,
            "fut_cvd": fut_cvd if fut_delta is not None else None,
            "spot_vol_buy": sb.get("buy"), "spot_vol_sell": sb.get("sell"),
            "spot_vol_total": (sb["buy"] + sb["sell"])
                if sb.get("buy") is not None and sb.get("sell") is not None else None,
            "spot_cvd": spot_cvd if spot_delta is not None else None,
        })
    return rows


# Columns added on top of COLUMNS for the extended "all charts" table.
RV_COL = "rv_7d"

# Per-chart checkbox -> extra columns added to the export when checked.
# Charts not listed here (funding, price, vol-*, cvd-*) are already covered by
# COLUMNS and are always exported. "oi" adds dynamic oi_ex_* columns.
CHART_EXTRA_COLS = {
    "oi": None,  # dynamic: oi_ex_<exchange> per _oi_exchange_map
    "vol-history": [RV_COL],
}
ALWAYS_COLS = ("timestamp", "datetime_utc")


def _oi_exchange_map(symbol, from_ts, to_ts):
    """Pick the OI-by-exchange series the way the dashboard does (d1 preferred,
    h1 fallback) and return (ts -> vals, ordered exchange names)."""
    h_pts = store.get_points(symbol, store.resolve_series("oi_exchange_h1"),
                             from_ts=from_ts, to_ts=to_ts)
    d_pts = store.get_points(symbol, store.resolve_series("oi_exchange_d1"),
                             from_ts=from_ts, to_ts=to_ts)
    pts = d_pts if d_pts else h_pts
    last_vals = pts[-1][1] if pts else {}
    names = sorted(
        {k for _, v in pts for k in v if not k.startswith("_")},
        key=lambda e: (-(last_vals.get(e) or 0), e),
    )
    return (dict(pts), names)


def _realized_vol_7d(rows):
    """Rolling 7-day realized vol (sample std of log returns, annualized %),
    matching the dashboard's historic-RV chart (drawVolHistory in app.js).
    Returns ts -> rv."""
    closes = [(r["timestamp"], r["spot_close"]) for r in rows
              if r.get("spot_close") is not None and r["spot_close"] > 0]
    out: dict[int, float] = {}
    win = 7
    if len(closes) <= win:
        return out
    ts = [t for t, _ in closes]
    val = [v for _, v in closes]
    for i in range(win, len(val)):
        rets = [math.log(val[j] / val[j - 1]) for j in range(i - win + 1, i + 1)]
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        out[ts[i]] = math.sqrt(var) * math.sqrt(365) * 100
    return out


def build_full_table(symbol: str, limit: int | None = None, timeframe: str = "d1",
                     from_ts: int | None = None,
                     to_ts: int | None = None) -> tuple[list[dict], list[str]]:
    """Merge every cached series for a symbol into one table covering ALL
    dashboard charts. Returns (rows, columns).

    Beyond :func:`build_table` it adds: rolling realized vol (RV history chart)
    and per-exchange OI (OI-by-exchange chart). The base ``COLUMNS`` order is
    preserved and extended.
    """
    symbol = symbol.upper()
    rows = build_table(symbol, limit=limit, timeframe=timeframe,
                       from_ts=from_ts, to_ts=to_ts)
    if not rows:
        return [], list(COLUMNS)

    oi_map, ex_names = _oi_exchange_map(symbol, from_ts, to_ts)
    oi_cols = [f"oi_ex_{e}" for e in ex_names]

    rv_map = _realized_vol_7d(rows)

    for r in rows:
        ts = r["timestamp"]
        r[RV_COL] = rv_map.get(ts)
        ox = oi_map.get(ts, {})
        for e in ex_names:
            r[f"oi_ex_{e}"] = ox.get(e)

    columns = list(COLUMNS) + [RV_COL] + oi_cols
    return rows, columns


# Chart ids the frontend checkboxes send; anything else is rejected by
# main.api_export.
EXPORT_CHARTS = set(CHART_EXTRA_COLS)


def select_extra_columns(charts: list[str], oi_names: list[str]) -> list[str]:
    """Resolve checked chart ids to the extra columns they contribute.

    ``oi`` expands to the dynamic per-exchange columns; unknown chart ids are
    ignored.
    """
    cols: list[str] = []
    for c in charts:
        fixed = CHART_EXTRA_COLS.get(c)
        if isinstance(fixed, list):
            cols.extend(f for f in fixed if f not in cols)
        elif fixed is None:
            cols.extend(f"oi_ex_{e}" for e in oi_names if f"oi_ex_{e}" not in cols)
    return cols


def build_export_table(symbol: str, limit: int | None = None,
                       timeframe: str = "d1", from_ts: int | None = None,
                       to_ts: int | None = None,
                       charts: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Merged export table for the API: base :data:`COLUMNS` plus the charts
    the user ticked. Returns (rows, column order)."""
    if not charts:
        return (build_table(symbol, limit=limit, timeframe=timeframe,
                            from_ts=from_ts, to_ts=to_ts), list(COLUMNS))
    rows, full_cols = build_full_table(symbol, limit=limit, timeframe=timeframe,
                                       from_ts=from_ts, to_ts=to_ts)
    oi_names = [c[len("oi_ex_"):] for c in full_cols if c.startswith("oi_ex_")]
    cols = list(COLUMNS) + select_extra_columns(charts, oi_names)
    return rows, cols


def to_csv(rows: list[dict], columns: list[str] | None = None) -> bytes:
    cols = columns or COLUMNS
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def to_xlsx(rows: list[dict], symbol: str, timeframe: str = "d1",
            columns: list[str] | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.comments import Comment

    cols = columns or COLUMNS
    wb = Workbook()
    ws = wb.active
    ws.title = f"{symbol} dashboard"
    ws.append(cols)
    # Notes (Excel comments) on each header cell that has a help entry.
    for j, c in enumerate(cols, start=1):
        help_text = col_help(c)
        if help_text:
            comment = Comment(help_text, "CoinGlass")
            comment.width = 256
            comment.height = 160
            ws.cell(row=1, column=j).comment = comment
    for r in rows:
        ws.append([r.get(c) for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_xls(rows: list[dict], symbol: str, timeframe: str = "d1",
           columns: list[str] | None = None) -> bytes:
    import xlwt

    cols = columns or COLUMNS
    wb = xlwt.Workbook()
    ws = wb.add_sheet(f"{symbol} dashboard"[:31])
    for j, c in enumerate(cols):
        ws.write(0, j, c)
    # .xls hard limit: 65536 rows
    for i, r in enumerate(rows[-65000:], start=1):
        for j, c in enumerate(cols):
            v = r.get(c)
            ws.write(i, j, v if v is not None else "")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
