# AGENTS.md

LLM project context. Read this first before touching the codebase. Keep prompts
focused on the relevant files for the task; do not load `data/` (SQLite cache),
`scraper/dev/` (one-off recon scripts), or the bundled JSON caches unless the
task explicitly needs them.

## Project Shape

- `scraper/fetch_bundle.js` — Node + puppeteer-core in-page scraper. Opens the
  CoinGlass currency page and calls the site's own decrypting API client
  (`__next_require__(94126)`). Writes ONE JSON bundle. Not a build step.
- `scraper/dev/` — throwaway recon/fuzz/probe scripts that found the endpoints.
  Not needed at runtime; ignore for normal work.
- `app/` — FastAPI backend.
  - `main.py` — routes, lifespan (db init), session middleware, router mount.
  - `fetcher.py` — runs the scraper in a background thread, normalizes the
    bundle into per-series rows, job registry (status bar), top-20 list.
  - `store.py` — SQLite cache; series-name ↔ timeframe resolution.
  - `exports.py` — merged CSV/XLSX/XLS table builder (`COLUMNS` is canonical).
  - `google_export.py` — Google Sheets OAuth2 web flow (router) + sheet creation.
  - `cli.py` — full CLI mirroring the web app (`python -m app <command>`:
    coins, refresh, stats, dashboard, table, price, analytics, export;
    `--json` everywhere, `--from/--to/--limit` windowing). No gsheet in CLI.
- `app/static/` — frontend with NO build step: `index.html`, `app.js`, `styles.css`.
  Served directly by FastAPI's `StaticFiles`.
- `app/tests/test_app.py` — self-contained `tmp_path` tests (no external services).
- `data/sqlite/coinglass_cache.db` — runtime cache state, not source.
- `Dockerfile`, `docker-compose.yml`, `rebuild.sh` — container build/run/redeploy.
- `requirements.txt` is the single source of Python deps; there is **no**
  `pyproject.toml`/poetry.

## Stable Contracts

- The dashboard is **daily-only**. The only meaningful timeframe is `d1`;
  `store.TF_SERIES` is `{"d1": {…}}` and every stored series carries the `_d1`
  suffix (`funding_d1`, `oi_agg_d1`, `oi_exchange_h1_d1`, …). `store.resolve_series(name)`
  is the only correct way to turn a base name into a stored name; it accepts an
  already-suffixed name or appends `_d1`. The `timeframe` arg still present in
  some signatures (`fetcher`, `resolve_series`, scraper arg 5) is kept for
  compatibility but always resolves to `d1`.
- Base series names: `funding, oi_agg, oi_exchange_h1, oi_exchange_d1,
  fut_buysell, spot_buysell, spot_price` (see `store.SERIES_NAMES`).
- Timestamps are stored and exchanged as **epoch seconds** (int). CoinGlass
  returns ms for OI-by-exchange `dateList`; `fetcher` divides by 1000.
- CVD is computed locally (cumulative `buy − sell`), not fetched.
- SQLite schema: `series_points(symbol, series, ts, vals JSON)` PK
  `(symbol, series, ts)`; `meta(key, value JSON, updated_at)` for cache TTL.
- Scraper → fetcher progress protocol (stderr): `PROGRESS <i> <n> <stage>`;
  `ERROR …` on failure. Job states: `running|done|error` (+ `fresh` for
  cache hits).
- Export `COLUMNS` (`app/exports.py`) is the canonical column order for
  CSV/XLSX/XLS/Google Sheets; the gsheet writer reuses it.
- Google export does **not** use a refresh token from `.env`; it uses the
  OAuth2 web flow in `app/google_export.py` with creds stored in the signed
  session cookie. `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are env; the OAuth
  redirect URI is `CG_GOOGLE_REDIRECT_URI` (defaults to `request.url_for`, set
  it explicitly when behind a proxy/domain/Docker port or Google returns
  `redirect_uri_mismatch`).

## Read The Smallest Useful Context

- Add/change a chart: the matching block in `app/static/app.js` (`drawDashboard`)
  + the series in `fetch_bundle.js` stages + `store.SERIES_NAMES`.
- Date range / windowing: `store.get_points` (`from_ts`/`to_ts`),
  `exports.build_table`, `main.py` endpoint params (`_validate_range` rejects
  `from_ts > to_ts` with 422), `rangeParams()`/`withRange()` in `app.js`.
- Cache/store: `app/store.py` only.
- Fetch lifecycle/status bar: `app/fetcher.py` (`_run_scraper`, job registry).
- Google Sheets export: `app/google_export.py`, `main.py` (`api_export` gsheet
  branch, router/middleware), `exportGSheet()` in `app.js`.
- Docker rebuild/redeploy: `rebuild.sh`, `docker-compose.yml`, `Dockerfile`.

## Verification

- Docker (primary):
  `docker compose run --rm --build test` (runs `pytest -q app/tests`).
  `./rebuild.sh` runs tests then rebuilds + restarts the app.
- Local:
  `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
  then `.venv/bin/pytest -q app/tests` (set `CG_DB_DIR` to a temp dir if needed).
- There is **no linter or type-checker** configured — `pytest` is the only
  automated gate. New code must keep the test suite green.
- **Headless chart check.** After any task that touches the frontend
  (`app/static/`), verify that charts actually render in any available headless
  browser: load the running app (`http://localhost:8081/`), collect
  `console.error`/`pageerror`, and wait until `main .chart svg` count reaches
  8 (one plot per chart; Plotly adds `.js-plotly-plot` to the chart div itself,
  so don't match that class — match `svg` inside `.chart`). Rendering can take
  a few seconds, so poll with a generous timeout. The scraper's
  `puppeteer-core` (`scraper/node_modules`) + host Chrome (`chromePath()` in
  `fetch_bundle.js`) work for this; a proven example is in the drag-and-drop
  verification flow (`/tmp/opencode/verify_dashboard.js` pattern).
- Google OAuth end-to-end can't run headless; the unit tests mock
  `googleapiclient.discovery.build`. A real check needs a browser and the
  callback URI registered in Google Cloud Console.

## Editing Rules

- Prefer focused changes over broad refactors. Mimic the existing terse style;
  no docstrings on trivial helpers, no comments unless behaviour is non-obvious.
- Do not commit `data/` (SQLite db, wal/shm) or `__pycache__`; they are runtime
  state. `.gitignore` keeps secrets out (`.env`).
- **Static cache-busting.** After editing `app/static/app.js` or
  `app/static/styles.css`, bump **that file's** `?v=<N>` query parameter in
  `app/static/index.html`. Versions are **independent per file**: bump only the
  one you changed. Increment the integer (`2 → 3`). The Plotly CDN URL is
  already versioned and needs no bump.
- When adding a series: update `fetch_bundle.js` stages, `store.SERIES_NAMES`
  + `TF_SERIES["d1"]`, `fetcher._normalize_bundle`, the relevant chart in `app.js`,
  and a test in `app/tests/test_app.py`.
- There is no timeframe selector in the UI and no per-request timeframe; do not
  re-introduce `h1`/`h4` series, a `tf-select`, or `pattern="^(h1|h4|d1)$"` query
  params — the app is daily-only by design.
- Stage only files relevant to the task for commits. Commit messages follow
  Conventional Commits in English (e.g. `feat: …`, `fix: …`). Commit the changes
  after completing each task (unless the user says otherwise): check `git status`
  / `git diff`, stage only the task's files, and make a focused commit.
