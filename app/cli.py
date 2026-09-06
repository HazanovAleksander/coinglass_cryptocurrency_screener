"""CLI mirroring the web dashboard's functionality.

Usage::

    python -m app <command> [options]

Commands: coins, refresh, stats, dashboard, table, price, analytics, export.
Reads the SQLite cache; ``refresh`` (and ``coins`` on a cache miss) run the
Node scraper. The dashboard is daily-only; Google Sheets export stays
web-only (OAuth browser flow).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import analytics, exports, fetcher, store


class CliError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- formatting

def _parse_iso(value: str) -> int:
    """Parse an ISO date/datetime (e.g. ``2024-01-01``) into epoch seconds.

    Naive values are interpreted as UTC.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _iso(ts: int | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _num(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return f"{int(v):,}"
    if isinstance(v, float):
        return f"{v:,.6g}"
    return str(v)


def _fmt_cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _fmt_pct(v) -> str:
    return "-" if v is None else f"{v:.4f}"


def _render(headers: list[str], rows: list[list[str]]) -> str:
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    out = []
    for i, row in enumerate(cells):
        out.append("  ".join(v.ljust(widths[j]) for j, v in enumerate(row)).rstrip())
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def _emit_table(headers: list[str], rows: list[list[str]]) -> None:
    print(_render(headers, rows))


def _emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _emit_matrix(symbols: list[str], matrix: list[list]) -> None:
    _emit_table([""] + symbols,
                [[a] + [_fmt_cell(v) for v in row]
                 for a, row in zip(symbols, matrix)])


# ---------------------------------------------------------------- helpers

def _window(args) -> tuple[int | None, int | None]:
    from_ts = to_ts = None
    if args.from_dt:
        try:
            from_ts = _parse_iso(args.from_dt)
        except ValueError:
            raise CliError(f"invalid ISO date: {args.from_dt}", code=2)
    if args.to_dt:
        try:
            to_ts = _parse_iso(args.to_dt)
        except ValueError:
            raise CliError(f"invalid ISO date: {args.to_dt}", code=2)
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise CliError("--from must be <= --to", code=2)
    return from_ts, to_ts


# ---------------------------------------------------------------- commands

def cmd_coins(args) -> int:
    coins = fetcher.get_top20(force=args.force)
    if args.json:
        _emit_json({"coins": coins})
        return 0
    _emit_table(
        ["symbol", "name", "price", "market_cap"],
        [[c["symbol"], c.get("name") or c["symbol"],
          _num(c.get("price")), _num(c.get("market_cap"))] for c in coins])
    return 0


def cmd_refresh(args) -> int:
    symbol = args.symbol.upper()

    def on_progress(cur, total, stage):
        print(f"\r{cur}/{total} {stage}", end="", flush=True, file=sys.stderr)

    job = fetcher.refresh_symbol_sync(
        symbol, force=args.force,
        on_progress=None if args.json else on_progress)
    if not args.json:
        print(file=sys.stderr)
    if job["state"] == "error":
        print(f"error: {job['error']}", file=sys.stderr)
        return 1
    last = store.get_meta(f"last_fetch:{symbol}")
    if args.json:
        _emit_json({**job, "last_fetch": last})
        return 0
    if job["state"] == "fresh":
        print(f"{symbol}: cache is fresh, scrape skipped (use --force)")
    else:
        print(f"{symbol}: fetched")
    counts = job.get("counts") or (last or {}).get("counts") or {}
    for name in sorted(counts):
        print(f"  {name}: {counts[name]} points")
    return 0


def cmd_stats(args) -> int:
    symbol = args.symbol.upper()
    stats = store.symbol_stats(symbol)
    last = store.get_meta(f"last_fetch:{symbol}")
    fresh = store.is_fresh(symbol, fetcher.STALE_DAYS)
    if args.json:
        _emit_json({"symbol": symbol, "series": stats, "fresh": fresh,
                    "last_fetch": last})
        return 0
    _emit_table(
        ["series", "points", "from", "to"],
        [[name, s["count"], _iso(s["min_ts"]), _iso(s["max_ts"])]
         for name in store.SERIES_NAMES
         for s in [stats.get(store.resolve_series(name))] if s])
    print()
    print(f"fresh: {'yes' if fresh else 'no'} (stale after {fetcher.STALE_DAYS}d)")
    if last:
        print(f"last_fetch: {_iso(last.get('ts'))} UTC")
    return 0


def cmd_dashboard(args) -> int:
    symbol = args.symbol.upper()
    from_ts, to_ts = _window(args)
    out = {"symbol": symbol, "timeframe": "d1", "series": {}}
    wanted = args.series or list(store.SERIES_NAMES)
    for name in wanted:
        pts = store.get_points(symbol, store.resolve_series(name),
                               limit=args.limit, from_ts=from_ts, to_ts=to_ts)
        out["series"][name] = [{"ts": ts, **vals} for ts, vals in pts]
    if not args.series:
        # OI by exchange: d1 preferred, h1 fallback (as /api/dashboard)
        h_pts = store.get_points(symbol, store.resolve_series("oi_exchange_h1"),
                                 limit=args.limit, from_ts=from_ts, to_ts=to_ts)
        d_pts = store.get_points(symbol, store.resolve_series("oi_exchange_d1"),
                                 limit=args.limit, from_ts=from_ts, to_ts=to_ts)
        oi_pts = (d_pts if d_pts else h_pts) or []
        out["series"]["oi_exchange"] = [{"ts": ts, **vals} for ts, vals in oi_pts]
    out["stats"] = store.symbol_stats(symbol)
    out["last_fetch"] = store.get_meta(f"last_fetch:{symbol}")
    if args.json:
        _emit_json(out)
        return 0
    for name, pts in out["series"].items():
        keys: list[str] = []
        for p in pts:
            for k in p:
                if k != "ts" and k not in keys:
                    keys.append(k)
        print(f"## {name} ({len(pts)} rows)")
        if pts:
            _emit_table(["datetime"] + keys,
                        [[_iso(p["ts"])] + [_num(p.get(k)) for k in keys]
                         for p in pts])
        print()
    return 0


def cmd_table(args) -> int:
    symbol = args.symbol.upper()
    from_ts, to_ts = _window(args)
    rows = exports.build_table(symbol, limit=args.limit,
                               from_ts=from_ts, to_ts=to_ts)
    if not rows:
        raise CliError(f"no cached data for {symbol}; "
                       f"run: python -m app refresh {symbol}")
    if args.json:
        _emit_json({"symbol": symbol, "timeframe": "d1", "rows": rows})
    elif args.csv:
        sys.stdout.buffer.write(exports.to_csv(rows, exports.COLUMNS))
    else:
        cols = list(exports.COLUMNS)
        _emit_table(cols, [[_num(r.get(c)) for c in cols] for r in rows])
    return 0


def cmd_price(args) -> int:
    symbol = args.symbol.upper()
    from_ts, to_ts = _window(args)
    pts = store.get_points(symbol, store.resolve_series("spot_price"),
                           limit=args.limit, from_ts=from_ts, to_ts=to_ts)
    fund = store.get_points(symbol, store.resolve_series("funding"),
                            limit=args.limit, from_ts=from_ts, to_ts=to_ts)
    out = {"symbol": symbol, "timeframe": "d1",
           "points": [{"ts": ts, "close": vals.get("close")} for ts, vals in pts],
           "funding": [{"ts": ts, "close": vals.get("close")} for ts, vals in fund]}
    if args.json:
        _emit_json(out)
        return 0
    fund_map = {p["ts"]: p["close"] for p in out["funding"]}
    _emit_table(["datetime", "close", "funding"],
                [[_iso(p["ts"]), _num(p["close"]), _num(fund_map.get(p["ts"]))]
                 for p in out["points"]])
    return 0


def cmd_analytics(args) -> int:
    from_ts, to_ts = _window(args)
    top = [c["symbol"] for c in fetcher.get_top20()]
    closes: dict[str, dict[int, float]] = {}
    funding: dict[str, dict[int, float]] = {}
    for sym in top:
        cl = analytics.series_closes(store.get_points(
            sym, store.resolve_series("spot_price"),
            limit=args.limit, from_ts=from_ts, to_ts=to_ts))
        if cl:
            closes[sym] = cl
        fu = analytics.series_closes(store.get_points(
            sym, store.resolve_series("funding"),
            limit=args.limit, from_ts=from_ts, to_ts=to_ts))
        if fu:
            funding[sym] = fu
    report = analytics.build_report(top, closes, funding,
                                    min_overlap=args.min_overlap)
    if args.json:
        _emit_json(report)
        return 0
    _emit_table(
        ["symbol", "days", "from", "to", "rv_mean", "rv_last",
         "funding_mean", "funding_last", "beta"],
        [[s["symbol"], s["days"], _iso(s["from_ts"]), _iso(s["to_ts"]),
          _fmt_pct(s["rv_mean"]), _fmt_pct(s["rv_last"]),
          _fmt_pct(s["funding_mean"]), _fmt_pct(s["funding_last"]),
          _fmt_cell(s["beta"])] for s in report["stats"]])
    if report["missing"]:
        print(f"\nmissing (no cached prices): {', '.join(report['missing'])}")
    for title, key in (("corr: returns", "returns"), ("corr: rv", "rv"),
                       ("corr: funding", "funding")):
        print(f"\n## {title}")
        _emit_matrix(report["symbols"], report["corr"][key])
    print("\n## cov: returns")
    _emit_matrix(report["symbols"], report["cov"]["returns"])
    print("\n## overlap (days)")
    _emit_matrix(report["symbols"], report["overlap"])
    return 0


def cmd_export(args) -> int:
    symbol = args.symbol.upper()
    from_ts, to_ts = _window(args)
    chart_ids = [c.strip() for c in args.charts.split(",")] if args.charts else []
    unknown = [c for c in chart_ids if c not in exports.EXPORT_CHARTS]
    if unknown:
        raise CliError(f"unknown chart id: {unknown[0]} "
                       f"(valid: {', '.join(sorted(exports.EXPORT_CHARTS))})",
                       code=2)
    if chart_ids:
        rows, columns = exports.build_export_table(
            symbol, limit=args.limit, timeframe="d1", from_ts=from_ts,
            to_ts=to_ts, charts=chart_ids)
    else:
        # default (no --charts): the old CLI behaviour — ALL charts
        rows, columns = exports.build_full_table(
            symbol, limit=args.limit, from_ts=from_ts, to_ts=to_ts)
    if not rows:
        raise CliError(f"no cached data for {symbol}; "
                       f"run: python -m app refresh {symbol}")
    if args.format == "csv":
        data = exports.to_csv(rows, columns)
    elif args.format == "xlsx":
        data = exports.to_xlsx(rows, symbol, "d1", columns)
    else:
        data = exports.to_xls(rows, symbol, "d1", columns)

    if args.output and args.output != "-":
        target = args.output
    elif args.format == "csv":
        target = None  # stdout
    else:
        target = f"{symbol}_dashboard_d1.{args.format}"

    if target is None:
        sys.stdout.buffer.write(data)
    else:
        with open(target, "wb") as f:
            f.write(data)
        print(f"wrote {len(rows)} rows to {target}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- parser

def _add_window(p: argparse.ArgumentParser, limit_default: int) -> None:
    p.add_argument("--limit", type=int, default=limit_default,
                   help=f"cap number of most-recent rows (default: {limit_default})")
    p.add_argument("--from", dest="from_dt", default=None, metavar="ISO",
                   help="window start, ISO date/datetime (e.g. 2024-01-01)")
    p.add_argument("--to", dest="to_dt", default=None, metavar="ISO",
                   help="window end, ISO date/datetime (e.g. 2024-12-31)")


def _add_json(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="output JSON")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app",
        description="CoinGlass dashboard CLI: mirrors the web app "
                    "(top-20, scraping, cached series, analytics, exports).")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    sp = sub.add_parser("coins", help="top-20 coins by market cap")
    sp.add_argument("--force", action="store_true",
                    help="bypass the daily top-20 cache")
    _add_json(sp)
    sp.set_defaults(func=cmd_coins)

    sp = sub.add_parser("refresh", help="scrape a symbol into the cache")
    sp.add_argument("symbol", help="coin symbol, e.g. BTC")
    sp.add_argument("--force", action="store_true",
                    help="scrape even if the cache is fresh")
    _add_json(sp)
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("stats", help="cached series overview for a symbol")
    sp.add_argument("symbol", help="coin symbol, e.g. BTC")
    _add_json(sp)
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("dashboard",
                        help="all cached series for a symbol (as /api/dashboard)")
    sp.add_argument("symbol", help="coin symbol, e.g. BTC")
    _add_window(sp, 500)
    sp.add_argument("--series", action="append", choices=store.SERIES_NAMES,
                    metavar="NAME",
                    help="restrict to a base series (repeatable)")
    _add_json(sp)
    sp.set_defaults(func=cmd_dashboard)

    sp = sub.add_parser("table", help="merged dashboard table (as /api/table)")
    sp.add_argument("symbol", help="coin symbol, e.g. BTC")
    _add_window(sp, 500)
    sp.add_argument("--csv", action="store_true",
                    help="CSV on stdout instead of a table")
    _add_json(sp)
    sp.set_defaults(func=cmd_table)

    sp = sub.add_parser("price", help="spot closes + funding (as /api/price)")
    sp.add_argument("symbol", help="coin symbol, e.g. BTC")
    _add_window(sp, 500)
    _add_json(sp)
    sp.set_defaults(func=cmd_price)

    sp = sub.add_parser("analytics",
                        help="cross-coin correlations (as /api/analytics/correlations)")
    _add_window(sp, 10000)
    sp.add_argument("--min-overlap", type=int, default=30,
                    help="min aligned observations per cell (default: 30)")
    _add_json(sp)
    sp.set_defaults(func=cmd_analytics)

    sp = sub.add_parser("export",
                        help="export the merged table (all charts) to CSV/XLSX/XLS")
    sp.add_argument("symbol", help="coin symbol, e.g. BTC")
    _add_window(sp, 5000)
    sp.add_argument("-o", "--output", default=None,
                    help="output file (default: stdout for csv, "
                         f"{{SYMBOL}}_dashboard_d1.{{ext}} for xlsx/xls)")
    sp.add_argument("-f", "--format", dest="format",
                    choices=["csv", "xlsx", "xls"], default="csv",
                    help="output format (default: csv)")
    sp.add_argument("--charts", default=None, metavar="IDS",
                    help="comma-separated chart extras: oi,vol-history")
    sp.set_defaults(func=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store.init_db()
    try:
        return args.func(args)
    except BrokenPipeError:
        # downstream closed the pipe (e.g. `... | head`); exit quietly
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
