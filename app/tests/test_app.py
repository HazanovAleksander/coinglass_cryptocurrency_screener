"""Tests for the CoinGlass dashboard app (self-contained tmp_path fixtures)."""
from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from google.oauth2.credentials import Credentials


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CG_DB_DIR", str(tmp_path / "sqlite"))
    # re-import with fresh env
    from app import store
    from app.main import app
    store.init_db()
    return TestClient(app)


def _seed(symbol="BTC", n=5):
    from app import store
    base = 1_785_000_000
    store.upsert_points(symbol, "spot_price_d1", [
        (base + i * 3600,
         {"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i,
          "volume": 1000 * (i + 1)})
        for i in range(n)
    ])
    store.upsert_points(symbol, "funding_d1", [
        (base + i * 3600,
         {"open": 0.001, "high": 0.002, "low": 0.0, "close": 0.0015,
          "price_open": 100.0, "price_close": 100.5})
        for i in range(n)
    ])
    store.upsert_points(symbol, "oi_agg_d1", [
        (base + i * 3600,
         {"open": 5e9, "high": 5.1e9, "low": 4.9e9, "close": 5e9 + i * 1e6})
        for i in range(n)
    ])
    store.upsert_points(symbol, "oi_exchange_h1_d1", [
        (base + i * 3600, {"Binance": 3e9, "OKX": 1e9, "_price": 100.0})
        for i in range(n)
    ])
    store.upsert_points(symbol, "fut_buysell_d1", [
        (base + i * 3600, {"buy": 200.0 + i, "sell": 150.0})
        for i in range(n)
    ])
    store.upsert_points(symbol, "spot_buysell_d1", [
        (base + i * 3600, {"buy": 20.0, "sell": 25.0})
        for i in range(n)
    ])


def test_store_roundtrip(client):
    from app import store
    _seed()
    pts = store.get_points("BTC", "spot_price_d1")
    assert len(pts) == 5
    ts, vals = pts[0]
    assert vals["open"] == 100
    # upsert same ts twice -> no duplicates
    store.upsert_points("BTC", "spot_price_d1", [(ts, {"open": 1})])
    assert len(store.get_points("BTC", "spot_price_d1")) == 5


def test_dashboard_api(client):
    _seed()
    r = client.get("/api/dashboard/btc")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTC"
    assert len(body["series"]["spot_price"]) == 5
    assert body["series"]["fut_buysell"][0]["buy"] == 200.0
    assert body["stats"]["funding_d1"]["count"] == 5


def test_table_merge_and_cvd(client):
    _seed()
    r = client.get("/api/table/BTC")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 5
    # futures CVD accumulates (buy-sell): 50, 101, 153, 206, 260
    assert rows[0]["fut_cvd"] == pytest.approx(50.0)
    assert rows[1]["fut_cvd"] == pytest.approx(101.0)
    # spot CVD negative accumulation
    assert rows[4]["spot_cvd"] == pytest.approx(-25.0)
    assert rows[0]["oi_total_usd"] == pytest.approx(4e9)
    assert rows[0]["spot_close"] == 100.5


def test_export_csv(client):
    _seed()
    r = client.get("/api/export/BTC.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.content.decode().strip().splitlines()
    assert len(lines) == 6  # header + 5 rows
    assert lines[0].startswith("timestamp,datetime_utc,spot_open")


def test_export_xlsx(client):
    _seed()
    r = client.get("/api/export/BTC.xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"  # zip magic


def test_export_xls(client):
    _seed()
    r = client.get("/api/export/BTC.xls")
    assert r.status_code == 200
    assert r.content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 magic


def test_export_gsheet_needs_auth(client):
    """First gsheet export has no session creds → 401 with an auth_url."""
    _seed()
    r = client.get("/api/export/BTC.gsheet")
    assert r.status_code == 401
    assert "auth_url" in r.json()


def test_export_no_data(client):
    r = client.get("/api/export/NOPE.csv")
    assert r.status_code == 404


def test_export_bad_format(client):
    _seed()
    r = client.get("/api/export/BTC.pdf")
    assert r.status_code == 400


def test_export_charts_unknown_id_422(client):
    _seed()
    r = client.get("/api/export/BTC.csv?charts=bogus")
    assert r.status_code == 422


def test_export_csv_charts_add_rv(client):
    from app import exports
    _seed()
    r = client.get("/api/export/BTC.csv?charts=vol-history")
    assert r.status_code == 200
    lines = r.content.decode().strip().splitlines()
    # base prefix preserved, rv_7d appended, no other chart extras
    assert lines[0].startswith("timestamp,datetime_utc,spot_open")
    assert "rv_7d" in lines[0]
    assert lines[0].endswith("spot_cvd,rv_7d")
    assert len(lines[0].split(",")) == len(exports.COLUMNS) + 1


def test_export_csv_charts_oi_by_exchange(client):
    from app import exports
    _seed()
    r = client.get("/api/export/BTC.csv?charts=oi")
    assert r.status_code == 200
    header = r.content.decode().splitlines()[0]
    assert "oi_ex_Binance" in header and "oi_ex_OKX" in header
    assert len(header.split(",")) > len(exports.COLUMNS)


def test_normalize_bundle(client):
    from app import fetcher, store
    bundle = {
        "symbol": "BTC",
        "series": {
            "funding": {"code": "0", "data": [
                {"data": [1785000000, "0.001", "0.002", "0.0005", "0.0015"],
                 "price": ["100", "101"]},
            ]},
            "oi_agg": {"code": "0", "data": [[1785000000, "5e9", "5.1e9", "4.9e9", "5.05e9"]]},
            "oi_by_exchange_hourly": {"code": "0", "data": {
                "dataMap": {"Binance": [1.0, 2.0], "OKX": [3.0, None]},
                "dateList": [1785000000000, 1785003600000],
                "priceList": [100.0, 101.0],
            }},
            "fut_buysell": {"code": "0", "data": [[1785000000, 10.0, 5.0]]},
            "spot_buysell": {"code": "0", "data": [[1785000000, 1.0, 2.0]]},
            "spot_price": {"code": "0", "data": [[1785000000, "1", "2", "0.5", "1.5", "99"]]},
        },
    }
    counts = fetcher._normalize_bundle("BTC", bundle)
    assert counts["funding_d1"] == 1
    assert counts["oi_exchange_h1_d1"] == 2
    pts = dict(store.get_points("BTC", "oi_exchange_h1_d1"))
    assert pts[1785000000]["Binance"] == 1.0
    assert pts[1785000000]["_price"] == 100.0
    assert pts[1785003600]["OKX"] is None


def test_normalize_bundle_d1_spot(client):
    from app import fetcher, store
    base = 1_784_937_600  # divisible by 86400 (1d) and 3600
    candles = [
        [base + i * 3600,
         str(100 + i), str(105 + i), str(95 + i), str(102 + i),
         str(1000 * (i + 1))]
        for i in range(26)
    ]
    bundle = {
        "symbol": "D1SPOT",
        "series": {
            "spot_price": {"code": "0", "data": candles},
        },
    }
    counts = fetcher._normalize_bundle("D1SPOT", bundle, "d1")
    assert "spot_price_d1" in counts
    pts = dict(store.get_points("D1SPOT", "spot_price_d1"))
    assert len(pts) == 2  # first full day + partial second day
    b0 = pts[base]
    assert b0["open"] == 100.0
    assert b0["high"] == 128.0
    assert b0["low"] == 95.0
    assert b0["close"] == 125.0
    assert b0["volume"] == 300000.0


def test_normalize_bundle_1000_quoted_kline(client):
    # 1000-quoted Binance klines (1000PEPEUSDT): the scraper rescales OHLC to
    # per-unit floats and leaves the USD volume as a string; OI stays in USD
    # (not rescaled) and padded leading rows carry nulls.
    from app import fetcher, store
    base = 1_683_504_000  # divisible by 86400
    bundle = {
        "symbol": "PEPE",
        "series": {
            "oi_agg": {"code": "0", "data": [
                [base, None, None, None, None],
                [base + 86400, 145493.513, 147736.61, 139823.734, 146034.157],
            ]},
            "spot_price": {"code": "0", "data": [
                [base, 2.2799e-06, 2.3888e-06, 1.5351e-06, 1.9464e-06, "1770085624.6857"],
                [base + 86400, 1.9464e-06, 2.1e-06, 1.9e-06, 2.05e-06, "165714069.5574592"],
            ]},
        },
    }
    counts = fetcher._normalize_bundle("PEPE", bundle, "d1")
    assert counts["spot_price_d1"] == 2
    assert counts["oi_agg_d1"] == 2
    pts = dict(store.get_points("PEPE", "spot_price_d1"))
    assert pts[base]["open"] == 2.2799e-06
    assert pts[base]["close"] == 1.9464e-06
    assert pts[base]["volume"] == 1770085624.6857
    oi = dict(store.get_points("PEPE", "oi_agg_d1"))
    assert oi[base]["close"] is None
    assert oi[base + 86400]["close"] == 146034.157


def test_job_registry(client):
    from app import fetcher
    job_id = fetcher._job_new("BTC")
    fetcher._job_update(job_id, current=3, total=9, stage="funding")
    j = fetcher.job_status(job_id)
    assert j["current"] == 3
    assert fetcher.running_job_for("BTC")["id"] == job_id
    fetcher._job_update(job_id, state="done")
    assert fetcher.running_job_for("BTC") is None


def test_coins_fallback(client, monkeypatch):
    from app import fetcher
    monkeypatch.setattr(fetcher, "NODE_BIN", "/nonexistent/node")
    coins = fetcher.get_top20(force=True)
    assert len(coins) == 22  # top-20 + PEPE + XAU + XAG
    assert coins[0]["symbol"] == "BTC"


BASE_TS = 1_785_000_000


def test_get_points_range(client):
    from app import store
    _seed()
    pts = store.get_points("BTC", "spot_price_d1",
                           from_ts=BASE_TS + 3600, to_ts=BASE_TS + 3 * 3600)
    assert [ts for ts, _ in pts] == [BASE_TS + 3600, BASE_TS + 2 * 3600, BASE_TS + 3 * 3600]


def test_dashboard_range_filter(client):
    _seed()
    r = client.get(f"/api/dashboard/BTC?from_ts={BASE_TS + 3600}&to_ts={BASE_TS + 3*3600}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["series"]["spot_price"]) == 3


def test_table_range_filter(client):
    _seed()
    r = client.get(f"/api/table/BTC?from_ts={BASE_TS + 2*3600}&to_ts={BASE_TS + 4*3600}")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 3


def test_dashboard_invalid_range_422(client):
    _seed()
    r = client.get(f"/api/dashboard/BTC?from_ts={BASE_TS + 3*3600}&to_ts={BASE_TS}")
    assert r.status_code == 422


def test_table_invalid_range_422(client):
    _seed()
    r = client.get(f"/api/table/BTC?from_ts={BASE_TS + 3*3600}&to_ts={BASE_TS}")
    assert r.status_code == 422


def test_export_invalid_range_422(client):
    _seed()
    r = client.get(f"/api/export/BTC.csv?from_ts={BASE_TS + 3*3600}&to_ts={BASE_TS}")
    assert r.status_code == 422


def test_dashboard_oi_prefers_daily(client):
    from app import store
    base = 1_785_000_000
    store.upsert_points("BTC", "oi_exchange_h1_d1", [
        (base + i * 86400, {"Binance": 1e9}) for i in range(3)])
    store.upsert_points("BTC", "oi_exchange_d1_d1", [
        (base + i * 86400, {"Binance": 2e9, "OKX": 1e9}) for i in range(100)])
    r = client.get("/api/dashboard/BTC")
    oi = r.json()["series"]["oi_exchange"]
    assert len(oi) == 100
    assert oi[0]["Binance"] == 2e9


# ------------------------------------------------------------------ refresh/cache-coverage

def test_refresh_fresh_when_cached(client, monkeypatch):
    from app import store, fetcher
    now = int(time.time())
    # current right edge (now) across key d1 series -> is_fresh -> no scrape
    for series in ("spot_price_d1", "funding_d1", "oi_agg_d1",
                   "fut_buysell_d1", "spot_buysell_d1"):
        store.upsert_points("BTC", series, [(now, {"close": 1.0})])
    called = []
    monkeypatch.setattr(fetcher, "_run_scraper", lambda *a, **k: called.append(a))
    r = client.post("/api/refresh/BTC")
    assert r.status_code == 200
    assert r.json()["state"] == "fresh"
    assert called == []


def test_refresh_force_bypasses_cache(client, monkeypatch):
    from app import store, fetcher
    store.upsert_points("BTC", "spot_price_d1",
                        [(int(time.time()), {"close": 1.0})])
    entered = threading.Event()
    release = threading.Event()

    def fake_scraper(*a, **k):
        entered.set()
        release.wait(timeout=5)
        return {"series": {}}

    monkeypatch.setattr(fetcher, "_run_scraper", fake_scraper)
    r = client.post("/api/refresh/BTC?force=true")
    assert r.status_code == 200
    j = r.json()
    assert j["state"] == "running"
    assert j["id"]
    entered.wait(timeout=5)
    release.set()
    time.sleep(0.1)


def test_refresh_range_not_covered_starts_job(client, monkeypatch):
    from app import fetcher
    entered = threading.Event()
    release = threading.Event()

    def fake_scraper(*a, **k):
        entered.set()
        release.wait(timeout=5)
        return {"series": {}}

    monkeypatch.setattr(fetcher, "_run_scraper", fake_scraper)
    r = client.post(
        f"/api/refresh/NOPE?from_ts={BASE_TS}&to_ts={BASE_TS + 2 * 86400}")
    assert r.status_code == 200
    j = r.json()
    assert j["state"] == "running"
    assert j["id"]
    entered.wait(timeout=5)
    release.set()
    time.sleep(0.1)


def test_is_fresh(client, monkeypatch):
    from app import store
    now = 1_800_000_000
    monkeypatch.setattr(store.time, "time", lambda: now)
    day = 86400

    # right edge at now across key series -> fresh
    store.upsert_points("COV", "spot_price_d1", [(now, {"close": 1.0})])
    store.upsert_points("COV", "funding_d1", [(now, {"close": 1.0})])
    assert store.is_fresh("COV", stale_days=1) is True
    assert store.is_fresh("GHOST", stale_days=1) is False

    # newest point 5 days old -> stale
    store.upsert_points("OLD", "spot_price_d1", [(now - 5 * day, {"close": 1.0})])
    assert store.is_fresh("OLD", stale_days=1) is False

    # short oi_exchange_h1_d1 fallback is excluded: alone it must not count
    store.upsert_points("H1", "oi_exchange_h1_d1", [(now, {"Binance": 1.0})])
    assert store.is_fresh("H1", stale_days=1) is False

    # exactly on the threshold (now - 1 day) is still fresh
    store.upsert_points("EDGE", "spot_price_d1", [(now - day, {"close": 1.0})])
    assert store.is_fresh("EDGE", stale_days=1) is True


# ------------------------------------------------------------------ google oauth

def _gs_request(session_creds=None):
    req = MagicMock()
    req.session = {}
    if session_creds:
        req.session["credentials"] = session_creds
    req.url_for.return_value = "/api/export/google/auth"
    return req


def _gs_creds_dict(expired=False, with_refresh=True):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expiry = (now - timedelta(hours=2)) if expired else (now + timedelta(hours=1))
    d = {
        "token": "ya29.stale" if expired else "ya29.fresh",
        "refresh_token": "1//refresh" if with_refresh else None,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "cs",
        "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
        "expiry": expiry.isoformat(),
    }
    return d


def _mock_sheets_service():
    service = MagicMock()
    sheets = MagicMock()
    create = MagicMock()
    create.execute.return_value = {
        "spreadsheetId": "abc123",
        "spreadsheetUrl": "https://sheets.google.com/abc123",
    }
    sheets.create.return_value = create
    vals = MagicMock()
    update = MagicMock()
    update.execute.return_value = {}
    vals.update.return_value = update
    sheets.values.return_value = vals
    batch = MagicMock()
    batch.execute.return_value = {}
    sheets.batchUpdate.return_value = batch
    service.spreadsheets.return_value = sheets
    return service


def test_xlsx_header_comments_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CG_DB_DIR", str(tmp_path / "sqlite"))
    from app import store
    from app import exports
    store.init_db()
    _seed()
    rows = exports.build_table("BTC")
    cols = list(exports.COLUMNS) + [exports.RV_COL]
    data = exports.to_xlsx(rows, "BTC", "h1", cols)
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    # every column with a COL_HELP entry carries a comment on its header cell
    commented = sum(1 for j, c in enumerate(cols, start=1)
                    if exports.COL_HELP.get(c) and ws.cell(row=1, column=j).comment)
    assert commented == len(exports.COL_HELP)


def test_credentials_to_dict_includes_expiry():
    from app.google_export import credentials_to_dict
    creds = Credentials(
        token="t", refresh_token="rt",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid", client_secret="cs",
        scopes=["s"], expiry=datetime(2026, 12, 31, 12, 0, 0),
    )
    d = credentials_to_dict(creds)
    assert d["expiry"] == "2026-12-31T12:00:00"
    assert d["refresh_token"] == "rt"


def test_create_sheet_missing_creds_returns_401():
    from app.google_export import create_sheet
    resp = create_sheet(_gs_request(), "BTC", "h1", [{"timestamp": 1}])
    assert resp.status_code == 401
    assert "auth_url" in json.loads(resp.body)


@patch("app.google_export.build")
def test_create_sheet_fresh_token_succeeds(mock_build):
    mock_build.return_value = _mock_sheets_service()
    from app.google_export import create_sheet
    resp = create_sheet(_gs_request(_gs_creds_dict(expired=False)), "BTC", "h1", [{"timestamp": 1}])
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["url"] == "https://sheets.google.com/abc123"
    # header-cell notes are pushed via batchUpdate
    service = mock_build.return_value
    service.spreadsheets().batchUpdate.assert_called_once()
    requests = service.spreadsheets().batchUpdate.call_args.kwargs["body"]["requests"]
    assert any("repeatCell" in r and r["repeatCell"]["cell"].get("note") for r in requests)


@patch("app.google_export.build")
def test_create_sheet_custom_columns(mock_build):
    mock_build.return_value = _mock_sheets_service()
    from app.google_export import create_sheet
    from app.exports import COL_HELP, col_help
    cols = ["timestamp", "rv_7d", "oi_ex_Binance"]
    resp = create_sheet(_gs_request(_gs_creds_dict(expired=False)), "BTC", "h1",
                        [{"timestamp": 1, "rv_7d": 5.5, "oi_ex_Binance": 7.0}], cols)
    assert resp.status_code == 200
    service = mock_build.return_value
    body = service.spreadsheets().values().update.call_args.kwargs["body"]
    assert body["values"][0] == cols
    assert body["values"][1] == [1, 5.5, 7.0]
    # extra (checkbox) columns carry header notes too, incl. dynamic oi_ex_*
    requests = service.spreadsheets().batchUpdate.call_args.kwargs["body"]["requests"]
    notes = {r["repeatCell"]["range"]["startColumnIndex"]: r["repeatCell"]["cell"]["note"]
             for r in requests if "repeatCell" in r}
    assert notes[1] == COL_HELP["rv_7d"]
    assert notes[2] == col_help("oi_ex_Binance")


@patch("app.google_export.build")
def test_create_sheet_stale_token_refreshes_and_persists(mock_build):
    mock_build.return_value = _mock_sheets_service()
    from app.google_export import create_sheet
    req = _gs_request(_gs_creds_dict(expired=True))
    with patch.object(Credentials, "refresh", return_value=None) as mock_ref, \
         patch("app.google_export.credentials_to_dict", return_value={"token": "refreshed!"}):
        resp = create_sheet(req, "BTC", "h1", [{"timestamp": 1}])
    assert resp.status_code == 200
    mock_ref.assert_called_once()
    assert req.session["credentials"] == {"token": "refreshed!"}
    # notes batchUpdate still runs after refresh
    service = mock_build.return_value
    service.spreadsheets().batchUpdate.assert_called_once()


def test_create_sheet_dead_refresh_token_returns_401():
    from google.auth.exceptions import RefreshError
    from app.google_export import create_sheet
    req = _gs_request(_gs_creds_dict(expired=True))
    with patch.object(Credentials, "refresh", side_effect=RefreshError("revoked")):
        resp = create_sheet(req, "BTC", "h1", [{"timestamp": 1}])
    assert resp.status_code == 401
    assert "auth_url" in json.loads(resp.body)
    assert "credentials" not in req.session


def test_create_sheet_no_refresh_token_invalid_returns_401():
    from app.google_export import create_sheet
    req = _gs_request(_gs_creds_dict(expired=True, with_refresh=False))
    resp = create_sheet(req, "BTC", "h1", [{"timestamp": 1}])
    assert resp.status_code == 401
    assert "credentials" not in req.session


def test_redirect_uri_uses_env_override(monkeypatch):
    from app.google_export import _redirect_uri, GOOGLE_REDIRECT_URI_ENV
    monkeypatch.setenv(GOOGLE_REDIRECT_URI_ENV, "https://pub.example.com/api/export/google/callback")
    req = _gs_request()
    assert _redirect_uri(req) == "https://pub.example.com/api/export/google/callback"
    req.url_for.assert_not_called()


def test_redirect_uri_falls_back_to_request_host(monkeypatch):
    from app.google_export import _redirect_uri, GOOGLE_REDIRECT_URI_ENV
    monkeypatch.delenv(GOOGLE_REDIRECT_URI_ENV, raising=False)
    req = _gs_request()
    req.url_for.return_value = "http://localhost:8081/api/export/google/callback"
    assert _redirect_uri(req) == "http://localhost:8081/api/export/google/callback"
    req.url_for.assert_called_once_with("google_callback")


def test_make_flow_disables_pkce(monkeypatch):
    # google-auth-oauthlib >=1.0 auto-enables PKCE, which Google "Web
    # application" clients reject ("Confirmation not sent"). The flow must not
    # put code_challenge in the consent URL.
    from app.google_export import _make_flow
    monkeypatch.delenv("CG_GOOGLE_REDIRECT_URI", raising=False)
    req = _gs_request()
    req.url_for.return_value = "http://localhost:8081/api/export/google/callback"
    flow = _make_flow(req)
    url, _state = flow.authorization_url(prompt="consent", access_type="offline")
    assert "code_challenge=" not in url
    assert "code_challenge_method=" not in url
    assert "state=" in url and "client_id=" in url and "redirect_uri=" in url


# ------------------------------------------------------------------ CLI / full table

@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CG_DB_DIR", str(tmp_path / "sqlite"))
    from app import store
    store.init_db()
    return tmp_path


def test_build_full_table_structure(cache_env):
    from app import exports
    _seed("BTC")
    rows, cols = exports.build_full_table("BTC")
    assert len(rows) == 5
    # base COLUMNS are preserved as a prefix
    assert cols[: len(exports.COLUMNS)] == exports.COLUMNS
    # OI-by-exchange columns (fallback to the hourly series that _seed writes)
    assert "oi_ex_Binance" in cols and "oi_ex_OKX" in cols
    assert "rv_7d" in cols
    # rv_7d needs 8 closes -> all empty here; per-exchange values populated
    assert all(r["rv_7d"] is None for r in rows)
    assert rows[0]["oi_ex_Binance"] == pytest.approx(3e9)
    assert rows[0]["oi_ex_OKX"] == pytest.approx(1e9)


def test_build_full_table_rv_7d(cache_env):
    import math
    import statistics

    from app import exports, store
    base = 1_700_000_000
    closes = [100, 101, 103, 106, 110, 105, 108, 112, 115, 111, 114, 120]
    store.upsert_points("RV", "spot_price_d1", [
        (base + i * 86400,
         {"open": c, "high": c, "low": c, "close": c, "volume": 1.0})
        for i, c in enumerate(closes)])
    rows, cols = exports.build_full_table("RV")
    assert "rv_7d" in cols
    # first 7 points have no rv (need 8 closes)
    assert all(r["rv_7d"] is None for r in rows[:7])
    # last row: recompute reference with sample std over the last 7 log returns
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    expected = statistics.stdev(rets[-7:]) * math.sqrt(365) * 100
    assert rows[-1]["rv_7d"] == pytest.approx(expected)
    assert rows[-1]["rv_7d"] > 0


def test_cli_csv_stdout(cache_env, capsysbinary):
    from app import cli
    _seed("BTC")
    assert cli.main(["export", "BTC"]) == 0
    out = capsysbinary.readouterr().out
    text = out.decode("utf-8") if isinstance(out, bytes) else out
    lines = text.strip().splitlines()
    assert lines[0].startswith("timestamp,datetime_utc,spot_open")
    assert "rv_7d" in lines[0] and "oi_ex_Binance" in lines[0]
    assert len(lines) == 6  # header + 5 rows


def test_cli_csv_file(cache_env):
    from app import cli
    _seed("BTC")
    out_file = cache_env / "out.csv"
    assert cli.main(["export", "BTC", "-o", str(out_file)]) == 0
    text = out_file.read_text(encoding="utf-8")
    lines = text.strip().splitlines()
    assert lines[0].startswith("timestamp,datetime_utc")
    assert "oi_ex_OKX" in lines[0]
    assert len(lines) == 6


def test_cli_no_data(cache_env, capsys):
    from app import cli
    assert cli.main(["export", "NOPE"]) == 1
    assert "no cached data" in capsys.readouterr().err


def test_cli_date_window(cache_env):
    from app import cli
    _seed("BTC")  # base=1_785_000_000, hourly, 5 points
    out_file = cache_env / "win.csv"
    # 2nd point onward (skip the first) — pass a full UTC datetime so the
    # date-only rounding doesn't pull the window below the first point
    start_iso = datetime.fromtimestamp(
        1_785_000_000 + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    assert cli.main(["export", "BTC", "--from", start_iso, "-o", str(out_file)]) == 0
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5  # header + 4 rows


def test_cli_export_unknown_chart_2(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["export", "BTC", "--charts", "bogus"]) == 2
    assert "unknown chart id" in capsys.readouterr().err


def test_cli_export_charts_select_columns(cache_env):
    from app import cli
    _seed("BTC")
    rv_file = cache_env / "rv.csv"
    assert cli.main(["export", "BTC", "--charts", "vol-history",
                     "-o", str(rv_file)]) == 0
    header = rv_file.read_text(encoding="utf-8").splitlines()[0]
    assert "rv_7d" in header and "oi_ex_" not in header
    oi_file = cache_env / "oi.csv"
    assert cli.main(["export", "BTC", "--charts", "oi", "-o", str(oi_file)]) == 0
    header = oi_file.read_text(encoding="utf-8").splitlines()[0]
    assert "oi_ex_Binance" in header and "rv_7d" not in header


# ------------------------------------------------------------------ CLI subcommands

def test_cli_coins_fallback(cache_env, monkeypatch, capsys):
    from app import cli, fetcher
    monkeypatch.setattr(fetcher, "NODE_BIN", "/nonexistent/node")
    assert cli.main(["coins"]) == 0
    out = capsys.readouterr().out
    assert "BTC" in out and "ETH" in out  # static fallback list


def test_cli_coins_json(cache_env, monkeypatch, capsys):
    from app import cli, fetcher
    monkeypatch.setattr(fetcher, "get_top20", lambda force=False: [
        {"symbol": "BTC", "name": "Bitcoin",
         "market_cap": 1.2e12, "price": 100000.5}])
    assert cli.main(["coins", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["coins"][0]["symbol"] == "BTC"
    assert data["coins"][0]["market_cap"] == pytest.approx(1.2e12)


def test_cli_stats(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["stats", "BTC"]) == 0
    out = capsys.readouterr().out
    assert "spot_price" in out and "funding" in out
    # BASE_TS is fixed in the past -> stale
    assert "fresh: no" in out
    assert "last_fetch" not in out


def test_cli_stats_json(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["stats", "BTC", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["symbol"] == "BTC"
    assert data["series"]["spot_price_d1"]["count"] == 5
    assert data["fresh"] is False
    assert data["last_fetch"] is None


def test_cli_dashboard_json(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["dashboard", "BTC", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["symbol"] == "BTC" and data["timeframe"] == "d1"
    assert len(data["series"]["spot_price"]) == 5
    # merged oi_exchange view: d1 preferred, h1 fallback (seeded h1 only)
    assert data["series"]["oi_exchange"][0]["Binance"] == pytest.approx(3e9)
    assert data["stats"]["funding_d1"]["count"] == 5


def test_cli_dashboard_series_filter(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["dashboard", "BTC", "--series", "spot_price", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert list(data["series"]) == ["spot_price"]


def test_cli_dashboard_pretty(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["dashboard", "BTC", "--series", "funding"]) == 0
    out = capsys.readouterr().out
    assert "## funding (5 rows)" in out
    assert "price_close" in out


def test_cli_table_json(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["table", "BTC", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["symbol"] == "BTC"
    assert data["rows"][0]["fut_cvd"] == pytest.approx(50.0)
    assert data["rows"][4]["spot_cvd"] == pytest.approx(-25.0)


def test_cli_table_csv(cache_env, capsysbinary):
    from app import cli
    _seed("BTC")
    assert cli.main(["table", "BTC", "--csv"]) == 0
    text = capsysbinary.readouterr().out.decode("utf-8")
    lines = text.strip().splitlines()
    assert lines[0].startswith("timestamp,datetime_utc,spot_open")
    assert len(lines) == 6


def test_cli_table_pretty(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["table", "BTC"]) == 0
    out = capsys.readouterr().out
    assert "datetime_utc" in out and "fut_cvd" in out


def test_cli_table_no_data(cache_env, capsys):
    from app import cli
    assert cli.main(["table", "NOPE"]) == 1
    assert "no cached data" in capsys.readouterr().err


def test_cli_price_json(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["price", "BTC", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["points"][0]["close"] == pytest.approx(100.5)
    assert data["funding"][0]["close"] == pytest.approx(0.0015)


def test_cli_price_pretty(cache_env, capsys):
    from app import cli
    _seed("BTC")
    assert cli.main(["price", "BTC"]) == 0
    out = capsys.readouterr().out
    assert "close" in out and "funding" in out
    assert "100.5" in out


def test_cli_window_validation(cache_env, capsys):
    from app import cli
    _seed("BTC")
    # from > to -> usage error (exit 2)
    assert cli.main(["table", "BTC", "--from", "2024-12-31",
                     "--to", "2024-01-01"]) == 2
    assert "--from must be <= --to" in capsys.readouterr().err
    # invalid ISO -> usage error (exit 2)
    assert cli.main(["table", "BTC", "--from", "not-a-date"]) == 2
    assert "invalid ISO date" in capsys.readouterr().err


def test_cli_refresh_fresh_skip(cache_env, monkeypatch, capsys):
    from app import cli, fetcher, store
    now = int(time.time())
    for series in ("spot_price_d1", "funding_d1", "oi_agg_d1",
                   "fut_buysell_d1", "spot_buysell_d1"):
        store.upsert_points("BTC", series, [(now, {"close": 1.0})])
    called = []
    monkeypatch.setattr(fetcher, "_run_scraper",
                        lambda *a, **k: called.append(a))
    assert cli.main(["refresh", "BTC"]) == 0
    assert called == []
    assert "fresh, scrape skipped" in capsys.readouterr().out


def test_cli_refresh_scrapes(cache_env, monkeypatch, capsys):
    from app import cli, fetcher

    def fake_scraper(symbol, limit, job_id, timeframe="d1", on_progress=None):
        if on_progress:
            on_progress(1, 2, "funding")
            on_progress(2, 2, "spot_price")
        return {"series": {
            "spot_price": {"code": "0",
                           "data": [[1_784_937_600, "1", "2", "0.5", "1.5", "10"]]},
            "funding": {"code": "0", "data": [
                {"data": [1_784_937_600, "0.001", "0.002", "0.0005", "0.0015"],
                 "price": ["1", "1.5"]}]},
        }}

    monkeypatch.setattr(fetcher, "_run_scraper", fake_scraper)
    assert cli.main(["refresh", "BTC"]) == 0
    captured = capsys.readouterr()
    assert "1/2 funding" in captured.err
    assert "fetched" in captured.out
    assert "spot_price_d1: 1 points" in captured.out
    assert "funding_d1: 1 points" in captured.out


def test_cli_refresh_json(cache_env, monkeypatch, capsys):
    from app import cli, fetcher, store

    def fake_scraper(symbol, limit, job_id, timeframe="d1", on_progress=None):
        return {"series": {
            "spot_price": {"code": "0",
                           "data": [[1_784_937_600, "1", "2", "0.5", "1.5", "10"]]},
        }}

    monkeypatch.setattr(fetcher, "_run_scraper", fake_scraper)
    assert cli.main(["refresh", "BTC", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "done"
    assert data["counts"]["spot_price_d1"] == 1
    assert data["last_fetch"]["counts"]["spot_price_d1"] == 1
    assert store.get_meta("last_fetch:BTC") is not None


def test_cli_refresh_error(cache_env, monkeypatch, capsys):
    from app import cli, fetcher

    def boom(*a, **k):
        raise RuntimeError("scraper exited rc=1")

    monkeypatch.setattr(fetcher, "_run_scraper", boom)
    assert cli.main(["refresh", "BTC"]) == 1
    assert "scraper exited rc=1" in capsys.readouterr().err


def test_cli_analytics_json(cache_env, capsys):
    from app import cli
    _top20_meta()
    _seed_analytics()
    assert cli.main(["analytics", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["symbols"] == ["BTC", "ETH", "DOGE"]
    assert data["missing"] == ["XRP", "PEPE", "XAU", "XAG"]
    assert data["base"] == "BTC"


def test_cli_analytics_pretty(cache_env, capsys):
    from app import cli
    _top20_meta()
    _seed_analytics()
    assert cli.main(["analytics"]) == 0
    out = capsys.readouterr().out
    assert "corr: returns" in out
    assert "corr: rv" in out
    assert "corr: funding" in out
    assert "cov: returns" in out
    assert "overlap (days)" in out
    assert "missing" in out and "XRP" in out


# ------------------------------------------------------------------ analytics

def test_analytics_pure_helpers(client):
    from app import analytics
    xs = [1, 2, 3, 4, 5]
    ys = [10, 20, 30, 40, 50]
    assert analytics.pearson(xs, ys) == pytest.approx(1.0)
    assert analytics.pearson(xs, [-v for v in ys]) == pytest.approx(-1.0)
    assert analytics.pearson([1, 1, 1], ys) is None
    assert analytics.covariance(xs, ys) == pytest.approx(25.0)
    assert analytics.beta(xs, ys) == pytest.approx(10.0)
    assert analytics.beta(xs, [1, 1, 1, 1, 1]) == 0.0  # flat y -> beta 0, not None
    assert analytics.beta([1, 1, 1], ys) is None


def test_analytics_log_returns_align_rv(client):
    import math
    from app import analytics
    r = analytics.log_returns({0: 100.0, 86400: 110.0, 172800: 99.0})
    assert r == {86400: math.log(1.1), 172800: math.log(0.9)}
    a, b = analytics.align({1: 1.0, 2: 2.0, 3: 3.0}, {2: 20.0, 3: 30.0, 4: 40.0})
    assert a == [2.0, 3.0] and b == [20.0, 30.0]
    closes = {i * 86400: 100.0 * (1.01 ** i) for i in range(12)}
    rv = analytics.rolling_rv(closes)
    # first sample at the 8th close, constant returns -> rv 0
    assert sorted(rv) == [i * 86400 for i in range(7, 12)]
    assert all(v == pytest.approx(0.0) for v in rv.values())


AN_BASE = 1_700_000_000
AN_DAY = 86400
AN_N = 45


def _seed_analytics():
    """BTC/ETH/DOGE with exactly linear return links: ETH = 2×BTC (corr 1,
    β 2), DOGE = −BTC (corr −1, β −1); XRP stays empty."""
    import math
    from app import store
    gs = [1.02, 0.99, 1.01]
    L = 0.0
    closes = {"BTC": [], "ETH": [], "DOGE": []}
    for i in range(AN_N):
        if i:
            L += math.log(gs[(i - 1) % 3])
        closes["BTC"].append(100 * math.exp(L))
        closes["ETH"].append(50 * math.exp(2 * L))
        closes["DOGE"].append(1 * math.exp(-L))
    for sym, cl in closes.items():
        store.upsert_points(sym, "spot_price_d1", [
            (AN_BASE + i * AN_DAY,
             {"open": c, "high": c, "low": c, "close": c, "volume": 1.0})
            for i, c in enumerate(cl)])
    for sym, k in (("BTC", 1.0), ("ETH", 2.0)):
        store.upsert_points(sym, "funding_d1", [
            (AN_BASE + i * AN_DAY, {"close": k * (0.001 + 0.0001 * (i % 5))})
            for i in range(AN_N)])


def _top20_meta():
    from app import store
    store.set_meta("top20", [
        {"symbol": s, "name": s, "market_cap": None, "price": None}
        for s in ("BTC", "ETH", "DOGE", "XRP", "WBTC", "USD1", "PEPE")
    ])


def test_price_api(client):
    _seed()
    r = client.get("/api/price/btc")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTC"
    assert len(body["points"]) == 5
    assert body["points"][0]["close"] == pytest.approx(100.5)
    assert len(body["funding"]) == 5
    assert body["funding"][0]["close"] == pytest.approx(0.0015)
    r = client.get(f"/api/price/BTC?from_ts={BASE_TS + 3600}")
    assert len(r.json()["points"]) == 4
    assert len(r.json()["funding"]) == 4


def test_price_api_invalid_range_422(client):
    _seed()
    r = client.get(f"/api/price/BTC?from_ts={BASE_TS + 3600}&to_ts={BASE_TS}")
    assert r.status_code == 422


def test_analytics_api(client):
    import math
    import statistics
    _top20_meta()
    _seed_analytics()
    r = client.get("/api/analytics/correlations")
    assert r.status_code == 200
    b = r.json()
    assert b["symbols"] == ["BTC", "ETH", "DOGE"]
    assert b["missing"] == ["XRP", "PEPE", "XAU", "XAG"]
    assert b["base"] == "BTC"
    assert b["overlap"][0][1] == AN_N - 1 and b["overlap"][1][0] == AN_N - 1
    assert b["corr"]["returns"][0][1] == pytest.approx(1.0)
    assert b["corr"]["returns"][0][2] == pytest.approx(-1.0)
    assert b["corr"]["returns"][1][0] == pytest.approx(1.0)
    gs = [1.02, 0.99, 1.01]
    rets = [math.log(gs[(i - 1) % 3]) for i in range(1, AN_N)]
    var_r = statistics.variance(rets)
    assert b["cov"]["returns"][0][1] == pytest.approx(2 * var_r)
    assert b["cov"]["returns"][0][0] == pytest.approx(var_r)
    assert b["corr"]["rv"][0][1] == pytest.approx(1.0)  # ETH rv = 2× BTC rv
    assert b["corr"]["funding"][0][1] == pytest.approx(1.0)  # ETH = 2× BTC
    assert b["corr"]["funding"][0][2] is None  # DOGE has no funding
    st = {s["symbol"]: s for s in b["stats"]}
    assert st["BTC"]["beta"] == 1.0
    assert st["ETH"]["beta"] == pytest.approx(2.0)
    assert st["DOGE"]["beta"] == pytest.approx(-1.0)
    assert st["BTC"]["days"] == AN_N
    assert st["BTC"]["funding_mean"] == pytest.approx(
        100 * statistics.mean(0.001 + 0.0001 * (i % 5) for i in range(AN_N)))
    assert st["DOGE"]["funding_mean"] is None


def test_analytics_min_overlap_nulls(client):
    _top20_meta()
    _seed_analytics()
    r = client.get("/api/analytics/correlations?min_overlap=100")
    assert r.status_code == 200
    b = r.json()
    assert b["corr"]["returns"][0][1] is None
    assert b["overlap"][0][1] == AN_N - 1  # overlap reported regardless
    assert b["corr"]["returns"][0][0] == 1.0  # diagonal always set


def test_analytics_empty_universe(client):
    _top20_meta()
    r = client.get("/api/analytics/correlations")
    assert r.status_code == 200
    b = r.json()
    assert b["symbols"] == [] and b["stats"] == []
    assert b["missing"] == ["BTC", "ETH", "DOGE", "XRP", "PEPE", "XAU", "XAG"]


def test_analytics_invalid_range_422(client):
    r = client.get(f"/api/analytics/correlations?from_ts={BASE_TS + 1}&to_ts={BASE_TS}")
    assert r.status_code == 422


# ------------------------------------------------------------------ spreads

SP_BASE = 1_700_000_000
SP_DAY = 86400


def _seed_spread():
    """BTC close [100,110,120,0] (last day untradeable), ETH [50,55,60,70];
    funding fully overlapping -> ratio skips the zero day, diff covers all 4."""
    from app import store
    store.upsert_points("BTC", "spot_price_d1", [
        (SP_BASE + i * SP_DAY, {"open": c, "high": c, "low": c, "close": c})
        for i, c in enumerate([100.0, 110.0, 120.0, 0.0])])
    store.upsert_points("ETH", "spot_price_d1", [
        (SP_BASE + i * SP_DAY, {"open": c, "high": c, "low": c, "close": c})
        for i, c in enumerate([50.0, 55.0, 60.0, 70.0])])
    store.upsert_points("BTC", "funding_d1", [
        (SP_BASE + i * SP_DAY, {"close": f})
        for i, f in enumerate([0.01, 0.02, 0.03, 0.04])])
    store.upsert_points("ETH", "funding_d1", [
        (SP_BASE + i * SP_DAY, {"close": f})
        for i, f in enumerate([0.005, 0.01, 0.015, 0.01])])


def test_spread_pure_function(client):
    from app import analytics
    base = {0: 100.0, 86400: 110.0, 172800: 120.0, 259200: 0.0, 345600: 130.0}
    quote = {0: 50.0, 86400: 55.0, 259200: 65.0, 345600: -1.0}
    bf = {0: 0.01, 86400: 0.02, 172800: 0.01}
    qf = {0: 0.005, 86400: 0.01, 172800: 0.015}
    out = analytics.build_spread(base, quote, bf, qf)
    # ratio: intersection where both > 0 -> day 0 and 1 only
    assert out["price_ratio"] == [
        {"ts": 0, "ratio": pytest.approx(2.0)},
        {"ts": 86400, "ratio": pytest.approx(2.0)},
    ]
    assert out["ratio_stats"] == {"days": 2, "from_ts": 0, "to_ts": 86400}
    # diff: intersection of funding dicts (days 0, 1, 2)
    assert out["funding_diff"] == [
        {"ts": 0, "diff": pytest.approx(0.005)},
        {"ts": 86400, "diff": pytest.approx(0.01)},
        {"ts": 172800, "diff": pytest.approx(-0.005)},
    ]
    assert out["diff_stats"]["days"] == 3
    assert analytics.build_spread({}, {}, {}, {}) == {
        "price_ratio": [], "funding_diff": [],
        "ratio_stats": {"days": 0, "from_ts": None, "to_ts": None},
        "diff_stats": {"days": 0, "from_ts": None, "to_ts": None},
    }


def test_spread_api(client):
    _seed_spread()
    r = client.get("/api/spread/btc/eth")
    assert r.status_code == 200
    b = r.json()
    assert b["base"] == "BTC" and b["quote"] == "ETH" and b["timeframe"] == "d1"
    assert b["cached"] == {"base": True, "quote": True}
    # zero BTC close on day 3 is excluded from the ratio, funding is not
    assert [p["ts"] for p in b["price_ratio"]] == [
        SP_BASE, SP_BASE + SP_DAY, SP_BASE + 2 * SP_DAY]
    assert all(p["ratio"] == pytest.approx(2.0) for p in b["price_ratio"])
    assert [p["diff"] for p in b["funding_diff"]] == \
        [pytest.approx(v) for v in (0.005, 0.01, 0.015, 0.03)]
    assert b["ratio_stats"]["days"] == 3 and b["diff_stats"]["days"] == 4


def test_spread_api_window_and_limit(client):
    _seed_spread()
    r = client.get(f"/api/spread/BTC/ETH?from_ts={SP_BASE + SP_DAY}")
    b = r.json()
    assert [p["ts"] for p in b["price_ratio"]] == [SP_BASE + SP_DAY, SP_BASE + 2 * SP_DAY]
    assert len(b["funding_diff"]) == 3
    # separate 15-day linear seed: ratio is flat 0.5, funding diff flat 0.005
    from app import store
    n = 15
    store.upsert_points("BTC", "spot_price_d1", [
        (SP_BASE + i * SP_DAY, {"close": float(i + 1)}) for i in range(n)])
    store.upsert_points("ETH", "spot_price_d1", [
        (SP_BASE + i * SP_DAY, {"close": 2.0 * (i + 1)}) for i in range(n)])
    store.upsert_points("BTC", "funding_d1", [
        (SP_BASE + i * SP_DAY, {"close": 0.01 + 0.001 * (i % 5)}) for i in range(n)])
    store.upsert_points("ETH", "funding_d1", [
        (SP_BASE + i * SP_DAY, {"close": 0.005 + 0.001 * (i % 5)}) for i in range(n)])
    r = client.get("/api/spread/BTC/ETH?limit=10")
    b = r.json()
    # limit keeps the most recent aligned points per series
    assert [p["ts"] for p in b["price_ratio"]] == [
        SP_BASE + i * SP_DAY for i in range(5, n)]
    assert all(p["ratio"] == pytest.approx(0.5) for p in b["price_ratio"])
    assert [p["ts"] for p in b["funding_diff"]] == [
        SP_BASE + i * SP_DAY for i in range(5, n)]
    assert all(p["diff"] == pytest.approx(0.005) for p in b["funding_diff"])


def test_spread_api_errors_and_empty(client):
    r = client.get("/api/spread/BTC/BTC")
    assert r.status_code == 422
    r = client.get(f"/api/spread/BTC/ETH?from_ts={SP_BASE + 1}&to_ts={SP_BASE}")
    assert r.status_code == 422
    r = client.get("/api/spread/XRP/PEPE")
    assert r.status_code == 200
    b = r.json()
    assert b["price_ratio"] == [] and b["funding_diff"] == []
    assert b["cached"] == {"base": False, "quote": False}


def test_cli_spread_json(cache_env, capsys):
    from app import cli
    _seed_spread()
    assert cli.main(["spread", "btc", "eth", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["base"] == "BTC" and data["quote"] == "ETH"
    assert len(data["price_ratio"]) == 3
    assert data["funding_diff"][0]["diff"] == pytest.approx(0.005)


def test_cli_spread_pretty_and_errors(cache_env, capsys):
    from app import cli
    _seed_spread()
    assert cli.main(["spread", "BTC", "ETH"]) == 0
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert "BTC/ETH" in lines[0] and "funding" in lines[0]
    assert lines[1].startswith("--")
    assert len(lines) == 2 + 4  # header + separator + one row per union ts
    assert cli.main(["spread", "BTC", "BTC"]) == 2
    assert "base and quote must differ" in capsys.readouterr().err
    assert cli.main(["spread", "XRP", "PEPE"]) == 1
    assert "no cached data" in capsys.readouterr().err


def test_top20_filters_stables_and_extras(client):
    from app import fetcher, store
    store.set_meta("top20", [
        {"symbol": s, "name": s, "market_cap": None, "price": None}
        for s in ("BTC", "WBTC", "USD1", "USDT", "ETH", "PEPE")
    ])
    syms = [c["symbol"] for c in fetcher.get_top20()]
    assert syms == ["BTC", "ETH", "PEPE", "XAU", "XAG"]
    r = client.get("/api/coins")
    assert r.status_code == 200
    assert [c["symbol"] for c in r.json()["coins"]] == ["BTC", "ETH", "PEPE", "XAU", "XAG"]


# ---------------------------------------------------------------- metals backfill

_BACKFILL_LBMA = [
    {"d": "2019-12-31", "v": [10.0, 8.0, None], "is_cms_locked": 0},  # before 2020
    {"d": "2024-01-02", "v": [20.0, 16.0, 18.0], "is_cms_locked": 0},
    {"d": "2024-01-03", "v": [21.0, 16.5, 18.5], "is_cms_locked": 0},
    {"d": "2024-01-04", "v": [None, 16.6, 18.6], "is_cms_locked": 0},  # no USD fix
    {"d": "2024-01-05", "v": [22.0, 17.0, 19.0], "is_cms_locked": 0},
]
_BACKFILL_INST = {"result": {"list": [{"symbol": "XAGUSDT", "launchTime": 1704067200000}]}
                  }  # launch 2024-01-01 UTC
_BACKFILL_KLINES = {"retCode": 0, "result": {"list": [  # newest first
    ["1704326400000", "30", "31", "29", "30.5", "10", "305.0"],  # 2024-01-04
    ["1704240000000", "29", "30", "28", "29.5", "11", "324.5"],  # 2024-01-03
]}}


def _mock_backfill_http(monkeypatch, lbma=_BACKFILL_LBMA):
    from app import backfill

    def fake_get_json(url, params=None):
        if "lbma" in url:
            return lbma
        if "instruments-info" in url:
            return _BACKFILL_INST
        return _BACKFILL_KLINES

    monkeypatch.setattr(backfill, "_get_json", fake_get_json)


def test_backfill_lbma_perp_merge(client, monkeypatch):
    from app import backfill, store
    _mock_backfill_http(monkeypatch)
    counts = backfill.backfill_spot("XAG")
    # boundary = first perp day (2024-01-03): LBMA keeps only 2024-01-02
    assert counts == {"spot_perp": 2, "spot_lbma": 1}
    pts = dict(store.get_points("XAG", "spot_price_d1"))
    assert len(pts) == 3
    jan3 = pts[1704240000]
    assert (jan3["open"], jan3["close"], jan3["volume"], jan3["src"]) == \
        (29.0, 29.5, 324.5, "perp")
    jan2 = pts[1704153600]
    assert (jan2["open"], jan2["high"], jan2["low"], jan2["close"]) == (20.0,) * 4
    assert jan2["src"] == "lbma" and jan2["volume"] is None
    # idempotent: LBMA fills gaps only, perp re-upserts (refreshes) its days
    counts = backfill.backfill_spot("XAG")
    assert counts == {"spot_perp": 2, "spot_lbma": 0}
    assert len(store.get_points("XAG", "spot_price_d1")) == 3


def test_backfill_no_perp_lbma_fills_all(client, monkeypatch):
    from app import backfill, store
    _mock_backfill_http(monkeypatch)

    def bybit_down(url, params=None):
        if "kline" in url or "instruments-info" in url:
            raise RuntimeError("bybit down")
        return _BACKFILL_LBMA

    monkeypatch.setattr(backfill, "_get_json", bybit_down)
    counts = backfill.backfill_spot("XAG")
    assert counts == {"spot_perp": 0, "spot_lbma": 3}
    pts = dict(store.get_points("XAG", "spot_price_d1"))
    assert pts[1704412800]["close"] == 22.0  # 2024-01-05 via LBMA
    # perp recovery replaces LBMA rows on its own days only
    _mock_backfill_http(monkeypatch)
    counts = backfill.backfill_spot("XAG")
    assert counts == {"spot_perp": 2, "spot_lbma": 0}
    pts = dict(store.get_points("XAG", "spot_price_d1"))
    assert pts[1704326400]["close"] == 30.5
    assert pts[1704153600]["close"] == 20.0


def test_backfill_skips_non_metals(client):
    from app import backfill
    assert backfill.backfill_spot("BTC") == {}


def test_top20_includes_metals_with_names(client):
    from app import fetcher, store
    store.set_meta("top20", [
        {"symbol": "BTC", "name": "Bitcoin", "market_cap": None, "price": None}])
    by = {c["symbol"]: c for c in fetcher.get_top20()}
    assert by["XAU"]["name"] == "Gold"
    assert by["XAG"]["name"] == "Silver"
