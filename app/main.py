"""FastAPI app: CoinGlass crypto dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present (not committed)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import analytics, exports, fetcher, google_export, store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="CoinGlass Dashboard", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("CG_SESSION_SECRET", "coinglass-dashboard-dev-secret"),
)
app.include_router(google_export.router)

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------- api

def _validate_range(from_ts: int | None, to_ts: int | None) -> None:
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(422, "from_ts must be <= to_ts")


@app.get("/api/coins")
def api_coins(force: bool = False):
    return {"coins": fetcher.get_top20(force=force)}


@app.post("/api/refresh/{symbol}")
def api_refresh(symbol: str, force: bool = False,
                from_ts: int | None = Query(None, ge=0),
                to_ts: int | None = Query(None, ge=0)):
    job = fetcher.refresh_symbol_async(symbol, force=force, from_ts=from_ts, to_ts=to_ts)
    return job


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    j = fetcher.job_status(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/api/dashboard/{symbol}")
def api_dashboard(
    symbol: str,
    limit: int = Query(500, ge=10, le=10000),
    from_ts: int | None = Query(None, ge=0),
    to_ts: int | None = Query(None, ge=0),
):
    symbol = symbol.upper()
    _validate_range(from_ts, to_ts)
    out = {"symbol": symbol, "timeframe": "d1", "series": {}}
    for name in store.SERIES_NAMES:
        series_name = store.resolve_series(name)
        pts = store.get_points(symbol, series_name, limit=limit, from_ts=from_ts, to_ts=to_ts)
        out["series"][name] = [{"ts": ts, **vals} for ts, vals in pts]
    # OI by exchange: prefer the daily series, which carries years of history
    # (hourly OI-by-exchange only spans a few days on CoinGlass).
    h_series = store.resolve_series("oi_exchange_h1")
    d_series = store.resolve_series("oi_exchange_d1")
    h_pts = store.get_points(symbol, h_series, limit=limit, from_ts=from_ts, to_ts=to_ts)
    d_pts = store.get_points(symbol, d_series, limit=limit, from_ts=from_ts, to_ts=to_ts)
    oi_pts = (d_pts if d_pts else h_pts) or []
    out["series"]["oi_exchange"] = [{"ts": ts, **vals} for ts, vals in oi_pts]
    out["stats"] = store.symbol_stats(symbol)
    last = store.get_meta(f"last_fetch:{symbol}")
    out["last_fetch"] = last
    return out


@app.get("/api/table/{symbol}")
def api_table(
    symbol: str,
    limit: int = Query(500, ge=10, le=50000),
    from_ts: int | None = Query(None, ge=0),
    to_ts: int | None = Query(None, ge=0),
):
    symbol = symbol.upper()
    _validate_range(from_ts, to_ts)
    return {"symbol": symbol, "timeframe": "d1",
            "rows": exports.build_table(symbol, limit=limit, from_ts=from_ts, to_ts=to_ts)}


@app.get("/api/available-series/{symbol}")
def api_available_series(symbol: str):
    symbol = symbol.upper()
    return {"symbol": symbol, "timeframe": "d1", "series": store.available_series_for(symbol)}


@app.get("/api/max-fetch-limit")
def api_max_fetch_limit():
    return {"max_limit": fetcher.MAX_FETCH_LIMIT, "default_limit": fetcher.FETCH_LIMIT}


@app.get("/api/price/{symbol}")
def api_price(
    symbol: str,
    limit: int = Query(500, ge=10, le=10000),
    from_ts: int | None = Query(None, ge=0),
    to_ts: int | None = Query(None, ge=0),
):
    symbol = symbol.upper()
    _validate_range(from_ts, to_ts)
    pts = store.get_points(symbol, store.resolve_series("spot_price"),
                           limit=limit, from_ts=from_ts, to_ts=to_ts)
    fund = store.get_points(symbol, store.resolve_series("funding"),
                            limit=limit, from_ts=from_ts, to_ts=to_ts)
    return {"symbol": symbol, "timeframe": "d1",
            "points": [{"ts": ts, "close": vals.get("close")} for ts, vals in pts],
            "funding": [{"ts": ts, "close": vals.get("close")} for ts, vals in fund]}


@app.get("/api/analytics/correlations")
def api_analytics_correlations(
    limit: int = Query(10000, ge=10, le=50000),
    from_ts: int | None = Query(None, ge=0),
    to_ts: int | None = Query(None, ge=0),
    min_overlap: int = Query(30, ge=2, le=10000),
):
    """Correlations/covariances + volatility/funding stats over the top-20.

    Universe is fixed to the (daily-cached) top-20 list; symbols without
    cached spot prices come back in ``missing``.
    """
    _validate_range(from_ts, to_ts)
    top = [c["symbol"] for c in fetcher.get_top20()]
    closes: dict[str, dict[int, float]] = {}
    funding: dict[str, dict[int, float]] = {}
    for sym in top:
        cl = analytics.series_closes(store.get_points(
            sym, store.resolve_series("spot_price"),
            limit=limit, from_ts=from_ts, to_ts=to_ts))
        if cl:
            closes[sym] = cl
        fu = analytics.series_closes(store.get_points(
            sym, store.resolve_series("funding"),
            limit=limit, from_ts=from_ts, to_ts=to_ts))
        if fu:
            funding[sym] = fu
    return analytics.build_report(top, closes, funding, min_overlap=min_overlap)


@app.get("/api/export/{symbol}.{fmt}")
def api_export(
    request: Request,
    symbol: str,
    fmt: str,
    limit: int = Query(5000, ge=10, le=100000),
    from_ts: int | None = Query(None, ge=0),
    to_ts: int | None = Query(None, ge=0),
    charts: str | None = Query(None, description="comma-separated chart ids to add to the base export"),
):
    symbol = symbol.upper()
    _validate_range(from_ts, to_ts)
    chart_ids = [c.strip() for c in charts.split(",")] if charts else []
    unknown = [c for c in chart_ids if c not in exports.EXPORT_CHARTS]
    if unknown:
        raise HTTPException(422, f"unknown chart id: {unknown[0]}")
    timeframe = "d1"
    rows, columns = exports.build_export_table(
        symbol, limit=limit, timeframe=timeframe, from_ts=from_ts,
        to_ts=to_ts, charts=chart_ids or None)
    if not rows:
        raise HTTPException(404, f"no cached data for {symbol}; refresh first")
    base = f"{symbol}_dashboard_{timeframe}"
    if fmt == "csv":
        return Response(
            exports.to_csv(rows, columns), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base}.csv"'})
    if fmt == "xlsx":
        return Response(
            exports.to_xlsx(rows, symbol, timeframe, columns),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'})
    if fmt == "xls":
        return Response(
            exports.to_xls(rows, symbol, timeframe, columns), media_type="application/vnd.ms-excel",
            headers={"Content-Disposition": f'attachment; filename="{base}.xls"'})
    if fmt == "gsheet":
        return google_export.create_sheet(request, symbol, timeframe, rows, columns)
    raise HTTPException(400, f"unsupported format: {fmt}")


# ---------------------------------------------------------------- frontend

@app.get("/")
def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp
