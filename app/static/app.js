/* CoinGlass Dashboard frontend */
"use strict";

const $ = (id) => document.getElementById(id);

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => { t = null; fn(...args); }, ms);
  };
}

const PLOT_LAYOUT = {
  dragmode: "zoom",
  paper_bgcolor: "#171e2e",
  plot_bgcolor: "#171e2e",
  font: { color: "#dbe2f0", size: 11 },
  margin: { l: 60, r: 60, t: 40, b: 40 },
  xaxis: {
    gridcolor: "#263149",
    showspikes: true,
    spikethickness: 1,
    spikedash: "dot",
    spikecolor: "#7e8aa8",
    spikemode: "toaxis+marker",
  },
  yaxis: { gridcolor: "#263149" },
  showlegend: true,
  legend: { orientation: "h", y: -0.15 },
};
const PLOT_CFG = { responsive: true, displaylogo: false };

let currentSymbol = null;
let pollTimer = null;
let maxFetchLimit = 4500;
let fetchLimit = 4500;
let defaultLayout = "layout-2";
let currentView = "dashboard";
let overlaySymbols = [];
let analyticsData = null;
let analyticsKey = null;
let anHighlight = "off";
const _overlayFetched = new Set();

const MAX_OVERLAYS = 5;
const BASE_COLOR = "#f5b942";
const OVERLAY_COLORS = ["#4f8cff", "#3fbf6f", "#b04fff", "#e05555", "#00c8d7"];

function setStatus(text, frac, isError) {
  const wrap = $("status-wrap");
  wrap.classList.remove("hidden");
  $("status-text").textContent = text;
  const bar = $("status-bar");
  bar.style.width = Math.round(100 * Math.max(0, Math.min(1, frac))) + "%";
  bar.classList.toggle("error", !!isError);
}

function hideStatus() {
  $("status-wrap").classList.add("hidden");
}

function applyLimitUI() {
  if (fetchLimit > maxFetchLimit || fetchLimit < 10) fetchLimit = maxFetchLimit;
  $("fetch-limit").value = fetchLimit;
}

async function loadFetchLimit() {
  try {
    const r = await jget("/api/max-fetch-limit");
    maxFetchLimit = Number(r.max_limit || r.default_limit || 1000);
    fetchLimit = Math.min(fetchLimit, maxFetchLimit);
    applyLimitUI();
  } catch (_e) {
    applyLimitUI();
  }
}

async function jget(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

function withTF(url) {
  // Dashboard is daily-only; kept as a no-op for call sites.
  return url;
}

function rangeParams() {
  const p = new URLSearchParams();
  const from = $("date-from").value;
  const to = $("date-to").value;
  if (from) {
    const ts = Math.floor(new Date(from + "T00:00:00Z").getTime() / 1000);
    if (Number.isFinite(ts) && ts > 0) p.set("from_ts", ts);
  }
  if (to) {
    const ts = Math.floor(new Date(to + "T23:59:59Z").getTime() / 1000);
    if (Number.isFinite(ts) && ts > 0) p.set("to_ts", ts);
  }
  return p;
}

function withRange(url) {
  const qs = rangeParams().toString();
  if (!qs) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}${qs}`;
}

// ------------------------------------------------------------- date presets

function dateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Quick date-range presets. Sets the panel inputs, then redraws (same path as
// manual date edits: data refresh -> chart redraw).
function applyPreset(key) {
  const today = new Date();
  let from = null;
  switch (key) {
    case "ytd":
      from = new Date(today.getFullYear(), 0, 1);
      break;
    case "1m":
      from = new Date(today);
      from.setMonth(from.getMonth() - 1);
      break;
    case "1y":
      from = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate());
      break;
    case "2y":
      from = new Date(today.getFullYear() - 2, today.getMonth(), today.getDate());
      break;
    case "all":
      from = new Date(2020, 0, 1);
      break;
  }
  $("date-from").value = from ? dateStr(from) : "";
  $("date-to").value = dateStr(today);
  if (currentSymbol) drawDashboard(currentSymbol);
  saveState();
}

// ------------------------------------------------------------- state persistence

const LS_KEY = "coinglass_dashboard";

// Checkbox per chart: adds that chart's data (OI per exchange, RV) to the
// export. Only these have extra data; the rest are already covered by the
// base columns and are rendered disabled.
const EXPORT_CHART_IDS = ["oi", "vol-history"];

function saveState() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      symbol: currentSymbol,
      dateFrom: $("date-from").value,
      dateTo: $("date-to").value,
      fetchLimit: fetchLimit,
      layout: defaultLayout,
      unitOrder: unitOrder(),
      view: currentView,
      overlays: overlaySymbols,
      anMetric: $("an-metric") ? $("an-metric").value : "returns",
      anMode: $("an-mode") ? $("an-mode").value : "corr",
      exportCharts: EXPORT_CHART_IDS.map((c) => {
        const el = document.querySelector(`.chart-export[data-chart="${CSS.escape(c)}"]`);
        return el ? el.checked : false;
      }),
    }));
  } catch (e) { /* localStorage unavailable */ }
}

function loadState() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;
    const s = JSON.parse(raw);
    const sel = $("coin-select");
    if (s.symbol && sel.querySelector(`option[value="${CSS.escape(s.symbol)}"]`))
      sel.value = s.symbol;
    if (s.dateFrom) $("date-from").value = s.dateFrom;
    if (s.dateTo) $("date-to").value = s.dateTo;
    if (s.fetchLimit) fetchLimit = s.fetchLimit;
    if (s.layout) defaultLayout = s.layout;
    if (s.view === "analytics") currentView = "analytics";
    if (Array.isArray(s.overlays)) overlaySymbols = s.overlays.filter((x) => typeof x === "string");
    else if (s.overlay) overlaySymbols = [s.overlay]; // migrate old single-overlay state
    overlaySymbols = overlaySymbols.slice(0, MAX_OVERLAYS);
    if (s.anMetric && $("an-metric")) $("an-metric").value = s.anMetric;
    if (s.anMode && $("an-mode")) $("an-mode").value = s.anMode;
    if (Array.isArray(s.unitOrder)) reorderUnits(s.unitOrder);
    if (Array.isArray(s.exportCharts)) {
      s.exportCharts.forEach((checked, i) => {
        const c = EXPORT_CHART_IDS[i];
        if (c) {
          const el = document.querySelector(`.chart-export[data-chart="${CSS.escape(c)}"]`);
          if (el) el.checked = !!checked;
        }
      });
    }
  } catch (e) { /* ignore */ }
}

// ------------------------------------------------------------- source links

const SRC_LINKS = {
  "chart-funding":  () => `https://www.coinglass.com/FundingRate`,
  "chart-oi":       () => `https://www.coinglass.com/OpenInterest`,
  "chart-vol-spot": () => `https://www.coinglass.com/Volume`,
  "chart-vol-fut":  () => `https://www.coinglass.com/Volume`,
  "chart-cvd-spot": (sym) => `https://www.coinglass.com/currencies/${encodeURIComponent(sym)}`,
  "chart-cvd-fut":  (sym) => `https://www.coinglass.com/currencies/${encodeURIComponent(sym)}`,
  "chart-price":    (sym) => `https://www.coinglass.com/currencies/${encodeURIComponent(sym)}`,
};

// All charts share the daily (d1) X domain of the selected coin, so every
// chart is kept in sync via linked zoom.
const SYNC_CHARTS = [
  "chart-funding", "chart-oi", "chart-vol-spot", "chart-vol-fut",
  "chart-cvd-spot", "chart-cvd-fut", "chart-price",
  "chart-vol-history",
];

// ------------------------------------------------------------- layout

// Default DOM order of movable units inside <main> (chart containers).
// Persisted as `unitOrder` in localStorage; reset restores this.
const DEFAULT_UNIT_ORDER = [
  "cc-funding", "cc-oi", "cc-vol-spot", "cc-vol-fut",
  "cc-cvd-spot", "cc-cvd-fut", "cc-price", "cc-vol-history",
];

function reorderUnits(order) {
  const main = document.querySelector("main");
  const byId = new Map();
  for (const el of main.children) byId.set(el.id, el);
  for (const id of order) {
    const el = byId.get(id);
    if (el) main.appendChild(el);
  }
}

function unitOrder() {
  return [...document.querySelector("main").children].map((el) => el.id);
}

function applyLayout(v) {
  defaultLayout = v;
  const main = document.querySelector("main");
  main.className = v;
  for (const id of SYNC_CHARTS) {
    const el = $(id);
    if (el) Plotly.Plots.resize(el);
  }
}

// ------------------------------------------------------------- coins

let _coinList = [];

async function loadCoins() {
  const sel = $("coin-select");
  try {
    const { coins } = await jget("/api/coins");
    _coinList = coins;
  } catch (e) {
    _coinList = [];
  }
  sel.innerHTML = "";
  for (const c of _coinList) {
    const opt = document.createElement("option");
    opt.value = c.symbol;
    opt.textContent = c.name && c.name !== c.symbol
      ? `${c.symbol} — ${c.name}` : c.symbol;
    sel.appendChild(opt);
  }
  if (!_coinList.length) sel.innerHTML = "<option value='BTC'>BTC</option>";
  populateOverlaySelect();
}

function overlayColor(i) {
  return OVERLAY_COLORS[i % OVERLAY_COLORS.length];
}

function renderOverlayChips() {
  const chips = $("overlay-chips");
  const reset = $("overlay-reset");
  if (!chips) return;
  chips.innerHTML = "";
  overlaySymbols.forEach((sym, i) => {
    const chip = document.createElement("span");
    chip.className = "overlay-chip";
    chip.style.borderColor = overlayColor(i);
    chip.title = `${sym}: Δ% от начала диапазона`;
    const dot = document.createElement("span");
    dot.className = "chip-dot";
    dot.style.background = overlayColor(i);
    const label = document.createElement("span");
    label.textContent = sym;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "chip-x";
    x.textContent = "×";
    x.title = `Убрать ${sym}`;
    x.addEventListener("click", () => removeOverlay(sym));
    chip.append(dot, label, x);
    chips.appendChild(chip);
  });
  if (reset) reset.hidden = overlaySymbols.length === 0;
}

function addOverlay(sym) {
  if (!sym || sym === currentSymbol) return;
  overlaySymbols = [...new Set([...overlaySymbols, sym])].slice(0, MAX_OVERLAYS);
  saveState();
  populateOverlaySelect();
  if (currentSymbol && currentView === "dashboard") drawDashboard(currentSymbol);
}

function removeOverlay(sym) {
  overlaySymbols = overlaySymbols.filter((s) => s !== sym);
  saveState();
  populateOverlaySelect();
  if (currentSymbol && currentView === "dashboard") drawDashboard(currentSymbol);
}

function resetOverlays() {
  overlaySymbols = [];
  saveState();
  populateOverlaySelect();
  if (currentSymbol && currentView === "dashboard") drawDashboard(currentSymbol);
}

function populateOverlaySelect() {
  const osel = $("overlay-coin");
  if (!osel) return;
  // базовая монета не может быть своим оверлеем — чистим при смене базы
  if (currentSymbol) overlaySymbols = overlaySymbols.filter((s) => s !== currentSymbol);
  const list = (_coinList.length
    ? _coinList.map((c) => c.symbol)
    : ["BTC"]).filter((s) => s !== currentSymbol && !overlaySymbols.includes(s));
  osel.innerHTML = "";
  const head = document.createElement("option");
  head.value = "";
  head.textContent = list.length && overlaySymbols.length < MAX_OVERLAYS
    ? "+ добавить…" : "макс. " + MAX_OVERLAYS;
  osel.appendChild(head);
  for (const s of list) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    osel.appendChild(opt);
  }
  osel.disabled = overlaySymbols.length >= MAX_OVERLAYS;
  osel.value = "";
  renderOverlayChips();
}

// ------------------------------------------------------------- fetch + poll

async function refreshSymbol(symbol, force) {
  const base = `/api/refresh/${encodeURIComponent(symbol)}?force=${force ? "true" : "false"}&limit=${encodeURIComponent(fetchLimit)}`;
  const job = await jget(withRange(base),
    { method: "POST" });
  if (job.state === "fresh") {
    setStatus("Данные в кэше (свежие)", 1, false);
    await drawDashboard(symbol);
    setTimeout(hideStatus, 1200);
    return;
  }
  if (job.state === "error") {
    setStatus("Ошибка: " + (job.error || "unknown"), 1, true);
    return;
  }
  pollJob(job.id, symbol);
}

function pollJob(jobId, symbol) {
  if (pollTimer) clearInterval(pollTimer);
  setStatus("Загрузка…", 0.02, false);
  pollTimer = setInterval(async () => {
    let j;
    try {
      j = await jget(`/api/job/${jobId}`);
    } catch (e) {
      clearInterval(pollTimer);
      setStatus("Ошибка опроса задачи", 1, true);
      return;
    }
    const frac = j.total ? j.current / j.total : 0;
    const stageNames = {
      browser_start: "запуск браузера", page_load: "загрузка страницы CoinGlass",
      funding: "funding rate", oi_agg: "открытый интерес (агрегат)",
      oi_by_exchange_hourly: "OI по биржам (час)", oi_by_exchange_daily: "OI по биржам (день)",
      fut_buysell: "объёмы фьючерсы", spot_buysell: "объёмы спот",
      spot_price: "котировки спот", done: "готово",
    };
    setStatus("Загрузка: " + (stageNames[j.stage] || j.stage), frac, false);
    if (j.state === "done") {
      clearInterval(pollTimer);
      setStatus("Готово", 1, false);
      await drawDashboard(symbol);
      setTimeout(hideStatus, 1500);
    } else if (j.state === "error") {
      clearInterval(pollTimer);
      setStatus("Ошибка: " + (j.error || "unknown"), 1, true);
    }
  }, 700);
}

// ------------------------------------------------------------- charts

function ts2date(arr) {
  return arr.map((p) => new Date(p.ts * 1000));
}

// CoinGlass pads buy/sell history with zero rows back to 2014; they carry no
// information and stretch the shared X axis over years of empty chart.
function trimLeadingZeroBuySell(pts) {
  let i = 0;
  while (i < pts.length && !(pts[i].buy || pts[i].sell)) i += 1;
  return i ? pts.slice(i) : pts;
}

// Simple 1D k-means (k=3) on a sorted-by-time series to find stable low/normal/
// high volatility regimes. Returns array of cluster labels (0=low,1=normal,2=high)
// aligned to input order. Falls back to nearest-mean assignment.
function kmeans3(x) {
  const arr = x.map((v) => (isFinite(v) ? v : 0));
  if (arr.length === 0) return [];
  const lo = Math.min(...arr), hi = Math.max(...arr);
  if (lo === hi) return arr.map(() => 1);
  // init centroids spread over [lo, hi]
  let c = [lo + (hi - lo) * 0.15, lo + (hi - lo) * 0.5, lo + (hi - lo) * 0.85];
  let labels = arr.map(() => 1);
  for (let iter = 0; iter < 25; iter++) {
    labels = arr.map((v) => {
      let best = 0, bd = Infinity;
      for (let k = 0; k < 3; k++) {
        const d = (v - c[k]) ** 2;
        if (d < bd) { bd = d; best = k; }
      }
      return best;
    });
    const sums = [0, 0, 0], cnts = [0, 0, 0];
    arr.forEach((v, i) => { sums[labels[i]] += v; cnts[labels[i]] += 1; });
    const nc = c.map((_old, k) => (cnts[k] ? sums[k] / cnts[k] : c[k]));
    if (nc.every((v, k) => Math.abs(v - c[k]) < 1e-9)) { c = nc; break; }
    c = nc;
  }
  // enforce ascending so 0=low,1=normal,2=high
  const order = [0, 1, 2].sort((a, b) => c[a] - c[b]);
  const remap = {};
  order.forEach((orig, rank) => { remap[orig] = rank; });
  return labels.map((l) => remap[l]);
}

// Convert a per-bar series (aligned with pts) to regime background shapes,
// one rect per contiguous run. `x` is array of Dates.
function regimeBands(pts, rv) {
  const labels = kmeans3(rv);
  const bands = [];
  let runStart = 0;
  for (let i = 1; i <= labels.length; i++) {
    if (i === labels.length || labels[i] !== labels[runStart]) {
      const z = labels[runStart];
      const color = z === 0 ? "#3fbf6f" : z === 1 ? "#f5b942" : "#e05555";
      bands.push({
        type: "rect", xref: "x", yref: "paper",
        x0: pts[runStart].ts * 1000, x1: (pts[i === labels.length ? i - 1 : i].ts + 1) * 1000,
        y0: 0, y1: 1, fillcolor: color, opacity: 0.09, line: { width: 0 }, layer: "below",
      });
      runStart = i;
    }
  }
  return bands;
}

// Historic realized volatility (annualized) of the selected coin + regime clustering.
async function drawVolHistory(symbol) {
  const el = $("chart-vol-history");
  if (!el) return;
  try {
    // Always source the daily price series: it carries years of history
    // (hourly spot_price only spans a few months), so RV covers the whole
    // range instead of a thin sliver at the right edge.
    const d = await jget(withRange(`/api/dashboard/${encodeURIComponent(symbol)}?limit=${maxFetchLimit}&timeframe=d1`));
    const pts = (d.series.spot_price && d.series.spot_price.length ? d.series.spot_price : []);
    if (pts.length < 2) { el.textContent = "Недостаточно данных для RV"; return; }
    // Rolling 7-day realized volatility: sample std of the trailing 7 daily
    // log returns.
    const RET_WIN = 7;
    const rv = [];
    for (let i = RET_WIN; i < pts.length; i++) {
      const rets = [];
      for (let j = i - RET_WIN + 1; j <= i; j++) {
        const c0 = pts[j - 1].close, c1 = pts[j].close;
        rets.push(c0 > 0 ? Math.log(c1 / c0) : 0);
      }
      const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
      const std = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1));
      rv.push(std);
    }
    const x = pts.slice(RET_WIN).map((p) => new Date(p.ts * 1000));
    const rvAnn = rv.map((r) => r * Math.sqrt(365) * 100);
    const bands = regimeBands(pts.slice(RET_WIN), rv);
    renderVolHistoryChart({ symbol, x, rvAnn, bands });
  } catch (e) {
    console.error("[drawVolHistory]", e);
    el.textContent = "Ошибка исторической волатильности: " + (e && e.message ? e.message : e);
  }
}

async function drawDashboard(symbol) {
  const tfTitle = "1d";
  const data = await jget(withRange(`/api/dashboard/${encodeURIComponent(symbol)}?limit=${maxFetchLimit}`));
  const s = data.series;
  s.fut_buysell = trimLeadingZeroBuySell(s.fut_buysell || []);
  s.spot_buysell = trimLeadingZeroBuySell(s.spot_buysell || []);

  // Общий диапазон дат по ВСЕМ сериям — чтобы оси X у всех графиков
  // были синхронизированы (иначе каждый график тянет свои ts из кэша
  // и диапазоны разъезжаются). oi_exchange исключён: его дневная история
  // тянется с 2020 и растягивала ось так, что часовые ряды (полгода)
  // сжимались в узкую полоску у правого края.
  const X_SERIES = ["funding", "fut_buysell", "spot_buysell", "spot_price"];
  let _tsAll = [];
  for (const name of X_SERIES) {
    const arr = s[name];
    if (Array.isArray(arr)) _tsAll = _tsAll.concat(arr.map((p) => p.ts));
  }
  if (!_tsAll.length) {
    for (const arr of Object.values(s)) {
      if (Array.isArray(arr)) _tsAll = _tsAll.concat(arr.map((p) => p.ts));
    }
  }
  const tMin = _tsAll.length ? Math.min(..._tsAll) : 0;
  const tMax = _tsAll.length ? Math.max(..._tsAll) : 1;
  const SHARED_X = { ...PLOT_LAYOUT.xaxis, type: "date", range: [new Date(tMin * 1000), new Date(tMax * 1000)], autorange: false };

  // Funding (абсолютный % или нормированное зеркало в режиме оверлея) — рендерится ниже вместе с оверлеями

  // OI by exchange (stacked area)
  {
    const pts = (s.oi_exchange && s.oi_exchange.length ? s.oi_exchange : []);
    const exNames = new Set();
    pts.forEach((p) => Object.keys(p).forEach((k) => {
      if (k !== "ts" && !k.startsWith("_")) exNames.add(k);
    }));
    const last = pts[pts.length - 1] || {};
    const sorted = [...exNames].sort((a, b) => (last[b] || 0) - (last[a] || 0));
    const top = sorted.slice(0, 8);
    const rest = sorted.slice(8);
    const x = ts2date(pts);
    const traces = top.map((ex) => ({
      x, y: pts.map((p) => p[ex] || 0), stackgroup: "oi",
      type: "scatter", mode: "lines", name: ex, line: { width: 0.5 },
    }));
    if (rest.length) {
      traces.push({
        x, y: pts.map((p) => rest.reduce((acc, ex) => acc + (p[ex] || 0), 0)), stackgroup: "oi", type: "scatter", mode: "lines", name: "Другие",
        line: { width: 0.5 },
      });
    }
    Plotly.react("chart-oi", traces,
      { ...PLOT_LAYOUT, xaxis: SHARED_X, title: `${symbol} · Open Interest по биржам, USD` }, PLOT_CFG);
  }

  // Volumes
  const volChart = (el, pts, title) => {
    Plotly.react(el, [
      { x: ts2date(pts), y: pts.map((p) => p.buy), type: "bar", name: "Buy",
        marker: { color: "#3fbf6f" } },
      { x: ts2date(pts), y: pts.map((p) => p.sell), type: "bar", name: "Sell",
        marker: { color: "#e05555" } },
    ], { ...PLOT_LAYOUT, xaxis: SHARED_X, barmode: "stack", title }, PLOT_CFG);
  };
  volChart("chart-vol-spot", s.spot_buysell || [], `${symbol} · Volume Spot, USD (${tfTitle})`);
  volChart("chart-vol-fut", s.fut_buysell || [], `${symbol} · Volume Futures, USD (${tfTitle})`);

  // CVD (cumulative buy-sell delta)
  const cvdChart = (el, pts, title, color) => {
    let acc = 0;
    const y = pts.map((p) => {
      if (p.buy != null && p.sell != null) acc += p.buy - p.sell;
      return acc;
    });
    Plotly.react(el, [{
      x: ts2date(pts), y, type: "scatter", mode: "lines", name: "CVD, USD",
      line: { color },
    }], { ...PLOT_LAYOUT, xaxis: SHARED_X, title }, PLOT_CFG);
  };
  cvdChart("chart-cvd-spot", s.spot_buysell || [], `${symbol} · CVD Spot (${tfTitle})`, "#4f8cff");
  cvdChart("chart-cvd-fut", s.fut_buysell || [], `${symbol} · CVD Futures (${tfTitle})`, "#b04fff");

  // Spot price candles (или мульти-оверлей монет, нормированных в начале диапазона)
  const activeOverlays = overlaySymbols.filter((sym) => sym !== symbol);
  let overlays = [];
  if (activeOverlays.length) {
    const fetched = await Promise.all(activeOverlays.map(async (sym) => {
      try {
        const od = await jget(withRange(`/api/price/${encodeURIComponent(sym)}?limit=${maxFetchLimit}`));
        return { sym, pts: (od.points || []).filter((p) => p.close != null && p.close > 0),
                 funding: (od.funding || []).filter((p) => p.close != null) };
      } catch (_e) { return { sym, pts: [], funding: [] }; }
    }));
    for (const o of fetched) {
      if (o.pts.length) overlays.push({ symbol: o.sym, pts: o.pts, funding: o.funding });
      else kickOverlayFetch(o.sym, symbol);
    }
  }
  renderSpotChart({ symbol, pts: s.spot_price || [], tfTitle, sharedX: SHARED_X, overlays });
  renderFundingChart({ symbol, pts: s.funding || [], sharedX: SHARED_X, overlays });

  // Update source links
  for (const [id, urlFn] of Object.entries(SRC_LINKS)) {
    const container = $(id)?.closest(".chart-container");
    const link = container?.querySelector(".src-link");
    if (link) link.href = urlFn(symbol);
  }

  const lf = data.last_fetch;
  $("meta-info").textContent = lf
    ? `Последнее обновление: ${new Date(lf.ts * 1000).toLocaleString()} · кэш: SQLite`
    : "Нет закэшированных данных — нажмите «Обновить данные»";

  // Историческая волатильность выбранной монеты + кластеризация режимов
  await drawVolHistory(symbol);

  // Linked zoom: все графики на единой дневной оси X выбранной монеты.
  for (const chartId of SYNC_CHARTS) {
    wireLinkedZoom(chartId);
    wireHoverSync(chartId);
  }
}

const _wiredCharts = new Set();
let _syncing = false;

// ------------------------------------------------------------- chart renderers

function renderSpotChart({ symbol, pts, tfTitle, sharedX, overlays = [] }) {
  if (!overlays.length) {
    Plotly.react("chart-price", [
      {
        x: ts2date(pts),
        open: pts.map((p) => p.open), high: pts.map((p) => p.high),
        low: pts.map((p) => p.low), close: pts.map((p) => p.close),
        type: "candlestick", name: symbol,
        increasing: { line: { color: "#3fbf6f" } },
        decreasing: { line: { color: "#e05555" } },
      },
    ], {
      ...PLOT_LAYOUT, title: `${symbol} · Котировки спот (${tfTitle})`,
      xaxis: { ...sharedX, rangeslider: { visible: false } },
    }, PLOT_CFG);
    return;
  }
  // Мульти-оверлей: каждая монета нормируется на свой первый close в диапазоне.
  const pct = (arr) => {
    const b = arr[0].close;
    return arr.map((p) => (p.close / b - 1) * 100);
  };
  const traces = [
    { x: ts2date(pts), y: pct(pts), type: "scatter", mode: "lines", name: symbol,
      line: { color: BASE_COLOR, width: 1.8 } },
    ...overlays.map((o, i) => ({
      x: ts2date(o.pts), y: pct(o.pts), type: "scatter", mode: "lines",
      name: o.symbol, line: { color: overlayColor(i), width: 1.5 },
    })),
  ];
  Plotly.react("chart-price", traces, {
    ...PLOT_LAYOUT,
    title: `${symbol} vs ${overlays.map((o) => o.symbol).join(", ")} · Δ% от начала диапазона`,
    xaxis: { ...sharedX, rangeslider: { visible: false } },
    yaxis: { ...PLOT_LAYOUT.yaxis, title: "Δ%" },
    shapes: [{
      type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 0, y1: 0,
      line: { color: "#7e8aa8", width: 1, dash: "dot" },
    }],
  }, PLOT_CFG);
}

// Funding: в оверлей-режиме у каждой монеты своя линия в абсолютных %
// (OI-Weighted Funding, close за сутки) — без нормировки и пропусков по f0.
// В легенде: средняя ставка за период и накопленный фандинг short-позиции
// (простая сумма Σ и сложный процент × с реинвестированием каждые сутки).
function fundingLegendStats(pts) {
  if (!pts.length) return "";
  const mean = pts.reduce((a, p) => a + p.close, 0) / pts.length;
  const sum = mean * pts.length;
  let comp = 1.0;
  for (const p of pts) comp *= 1 + p.close / 100;
  const fmt = (v) => (v >= 0 ? "+" : "") + v.toFixed(4);
  return ` · ср ${fmt(mean)}%/д · short Σ ${fmt(sum)}% · × ${fmt((comp - 1) * 100)}%`;
}

function renderFundingChart({ symbol, pts, sharedX, overlays = [] }) {
  const basePts = (pts || []).filter((p) => p.close != null);
  if (!overlays.length) {
    Plotly.react("chart-funding", [{
      x: ts2date(basePts), y: basePts.map((p) => p.close),
      type: "scatter", mode: "lines",
      name: `Funding, %${fundingLegendStats(basePts)}`,
      line: { color: BASE_COLOR },
    }], { ...PLOT_LAYOUT, xaxis: sharedX,
         title: `${symbol} · OI-Weighted Funding Rate (1d)` }, PLOT_CFG);
    return;
  }
  const traces = [];
  if (basePts.length) {
    traces.push({ x: ts2date(basePts), y: basePts.map((p) => p.close),
      type: "scatter", mode: "lines",
      name: `${symbol}${fundingLegendStats(basePts)}`,
      line: { color: BASE_COLOR, width: 1.8 } });
  }
  overlays.forEach((o, i) => {
    if (!o.funding.length) return;
    traces.push({ x: ts2date(o.funding), y: o.funding.map((p) => p.close),
      type: "scatter", mode: "lines",
      name: `${o.symbol}${fundingLegendStats(o.funding)}`,
      line: { color: overlayColor(i), width: 1.5 } });
  });
  Plotly.react("chart-funding", traces, {
    ...PLOT_LAYOUT,
    title: `${symbol} vs ${overlays.map((o) => o.symbol).join(", ")} · Funding Rate, % (1d)`,
    xaxis: sharedX,
    yaxis: { ...PLOT_LAYOUT.yaxis, title: "%" },
  }, PLOT_CFG);
}

// Оверлей-монета без кэша: тихо запустить фоновую загрузку и перерисовать,
// когда данные появятся (по одной попытке на монету за сессию).
function kickOverlayFetch(sym, forSymbol) {
  if (_overlayFetched.has(sym)) return;
  _overlayFetched.add(sym);
  (async () => {
    try {
      const job = await jget(`/api/refresh/${encodeURIComponent(sym)}`, { method: "POST" });
      if (job.state === "running" && job.id) await waitJobDone(job.id);
    } catch (_e) { return; }
    if (currentSymbol === forSymbol && overlaySymbols.includes(sym) && currentView === "dashboard")
      drawDashboard(forSymbol);
  })();
}

function waitJobDone(jobId, timeoutMs = 900000) {
  return new Promise((resolve) => {
    const started = Date.now();
    const t = setInterval(async () => {
      let j = null;
      try { j = await jget(`/api/job/${jobId}`); } catch (_e) { /* keep polling */ }
      if (Date.now() - started > timeoutMs || (j && j.state !== "running")) {
        clearInterval(t);
        resolve(j);
      }
    }, 1500);
  });
}

function renderVolHistoryChart({ symbol, x, rvAnn, bands }) {
  const traces = [
    {
      x, y: rvAnn, type: "scatter", mode: "lines", name: "RV (realized, ann. %)",
      line: { color: "#4f8cff", width: 1.4 }, fill: "tozeroy", fillcolor: "rgba(79,140,255,0.12)",
      yaxis: "y",
    },
  ];
  Plotly.react("chart-vol-history", traces, {
    ...PLOT_LAYOUT,
    title: `${symbol} · Историческая волатильность + кластеризация режимов`,
    xaxis: { ...PLOT_LAYOUT.xaxis, type: "date" },
    shapes: bands,
    yaxis: { ...PLOT_LAYOUT.yaxis, title: "RV, % годовых" },
  }, PLOT_CFG);
}

// Synced crosshair: hovering any date-axis chart draws its vertical spike
// (to the X axis) on every other chart in SYNC_CHARTS at the same date.
let _hoverSyncing = false;

function wireHoverSync(chartId) {
  const el = $(chartId);
  if (!el || typeof el.on !== "function") return;
  if (el.dataset.hoverSyncWired) return;
  el.dataset.hoverSyncWired = "1";

  const broadcast = (xms) => {
    for (const id of SYNC_CHARTS) {
      if (id === chartId) continue;
      const other = $(id);
      if (other) Plotly.Fx.hover(other, xms === null ? [] : [{ curveNumber: 0, xval: xms }]);
    }
  };

  el.on("plotly_hover", (data) => {
    if (_hoverSyncing) return;
    const pt = data.points && data.points[0];
    if (!pt || pt.x === undefined) return;
    const xms = pt.x instanceof Date ? pt.x.getTime() : new Date(pt.x).getTime();
    if (!Number.isFinite(xms)) return;
    _hoverSyncing = true;
    try {
      broadcast(xms);
    } finally {
      _hoverSyncing = false;
    }
  });

  el.on("plotly_unhover", () => {
    if (_hoverSyncing) return;
    _hoverSyncing = true;
    try {
      broadcast(null);
    } finally {
      _hoverSyncing = false;
    }
  });
}

function wireLinkedZoom(chartId) {
  const el = $(chartId);
  if (!el || typeof el.on !== "function") return;
  el.on("plotly_relayout", async (ed) => {
    if (_syncing || !ed) return;
    const others = SYNC_CHARTS.filter((id) => id !== chartId).map($).filter(Boolean);
    _syncing = true;
    try {
      if (ed["xaxis.autorange"]) {
        await Promise.all(others.map((o) => Plotly.relayout(o, { "xaxis.autorange": true })));
        $("date-from").value = "";
        $("date-to").value = "";
      } else {
        let r0 = ed["xaxis.range[0]"], r1 = ed["xaxis.range[1]"];
        if (r0 === undefined || r1 === undefined) {
          if (Array.isArray(ed["xaxis.range"])) [r0, r1] = ed["xaxis.range"];
          else return;
        }
        await Promise.all(others.map((o) => Plotly.relayout(o, { "xaxis.range[0]": r0, "xaxis.range[1]": r1 })));
        const toDay = (v) => {
          try { return new Date(v).toISOString().slice(0, 10); } catch (_) { return ""; }
        };
        $("date-from").value = toDay(r0);
        $("date-to").value = toDay(r1);
      }
      saveState();
    } finally {
      _syncing = false;
    }
  });
  _wiredCharts.add(chartId);
}


// ------------------------------------------------------------- export

function exportUrl(symbol, fmt) {
  const url = withRange(withTF(`/api/export/${encodeURIComponent(symbol)}.${fmt}`));
  const charts = selectedExportCharts();
  if (!charts.length) return url;
  return `${url}${url.includes("?") ? "&" : "?"}charts=${charts.join(",")}`;
}

function selectedExportCharts() {
  return [...document.querySelectorAll(".chart-export:not(:disabled):checked")]
    .map((el) => el.dataset.chart)
    .filter(Boolean);
}

async function exportGSheet() {
  const symbol = currentSymbol;
  if (!symbol) return;
  const url = () => exportUrl(symbol, "gsheet");
  setStatus("Экспорт в Google Sheets…", 0.5, false);
  try {
    let resp = await fetch(url());
    if (resp.status === 401) {
      const data = await resp.json();
      const popup = window.open(data.auth_url, "Google Auth", "width=500,height=600");
      if (!popup) {
        setStatus("Разрешите всплывающие окна для авторизации Google", 1, true);
        return;
      }
      const timer = setInterval(async () => {
        if (popup.closed) {
          clearInterval(timer);
          const retry = await fetch(url());
          if (retry.ok) {
            const res = await retry.json();
            setStatus("Google Sheet создан", 1, false);
            if (res.url) window.open(res.url, "_blank");
            setTimeout(hideStatus, 1500);
          } else {
            const e = await retry.json().catch(() => ({}));
            setStatus("Google Sheets: " + (e.error || e.detail || retry.statusText), 1, true);
          }
        }
      }, 1000);
      return;
    }
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.error || e.detail || resp.statusText);
    }
    const res = await resp.json();
    setStatus("Google Sheet создан", 1, false);
    if (res.url) window.open(res.url, "_blank");
    setTimeout(hideStatus, 1500);
  } catch (e) {
    setStatus("Google Sheets: " + e.message, 1, true);
  }
}

async function doExport(fmt) {
  const symbol = currentSymbol;
  if (!symbol) return;
  if (fmt === "gsheet") return exportGSheet();
  window.location.href = exportUrl(symbol, fmt);
}

// ------------------------------------------------------------- analytics tab

function setView(v) {
  currentView = v;
  $("tab-dashboard").classList.toggle("active", v === "dashboard");
  $("tab-analytics").classList.toggle("active", v === "analytics");
  document.querySelector("main").toggleAttribute("hidden", v !== "dashboard");
  $("analytics-view").classList.toggle("hidden", v === "dashboard");
  document.querySelectorAll(".dash-only").forEach((el) =>
    el.toggleAttribute("hidden", v !== "dashboard"));
  if (v === "analytics") {
    ensureAnalytics();
    if ($("an-heatmap").classList.contains("js-plotly-plot"))
      Plotly.Plots.resize($("an-heatmap"));
  } else {
    applyLayout(defaultLayout); // resize charts back after display:none
  }
  saveState();
}

function ensureAnalytics() {
  const key = rangeParams().toString();
  if (!analyticsData || analyticsKey !== key) loadAnalytics();
}

async function loadAnalytics() {
  const el = $("an-heatmap");
  // не портить живой график текстом-плейсхолдером: Plotly не удаляет чужие
  // текстовые узлы в контейнере, и «Загрузка…» зависала бы над heatmap
  if (!el.classList.contains("js-plotly-plot")) el.textContent = "Загрузка…";
  try {
    analyticsKey = rangeParams().toString();
    analyticsData = await jget(withRange("/api/analytics/correlations"));
    renderAnalytics();
  } catch (e) {
    el.textContent = "Ошибка анализа: " + (e && e.message ? e.message : e);
  }
}

function fmtCell(v, mode) {
  if (v == null || !Number.isFinite(v)) return "";
  return mode === "cov" ? v.toExponential(1) : v.toFixed(2);
}

function highlightPairs(d, metric) {
  if (anHighlight === "off") return [];
  const th = anHighlight === "high" ? 0.7 : 0.3;
  const z = d.corr[metric];
  const pairs = [];
  for (let i = 0; i < z.length; i++) {
    for (let j = i + 1; j < z.length; j++) {
      const v = z[i][j];
      if (v == null || !Number.isFinite(v)) continue;
      if (anHighlight === "high" ? v >= th : v <= th) pairs.push({ i, j, v });
    }
  }
  return pairs;
}

function pairShapes(pairs) {
  const color = anHighlight === "low" ? "#f85149" : "#3fb950";
  const shapes = [];
  for (const { i, j } of pairs) {
    for (const [a, b] of [[j, i], [i, j]]) {
      shapes.push({
        type: "rect", xref: "x", yref: "y", layer: "above",
        x0: a - 0.5, x1: a + 0.5, y0: b - 0.5, y1: b + 0.5,
        line: { color, width: 3 },
      });
    }
  }
  return shapes;
}

function updatePairsInfo(pairs, syms) {
  const el = $("an-pairs");
  if (anHighlight === "off") { el.textContent = ""; return; }
  if (!pairs.length) {
    el.textContent = `Нет пар с корреляцией ${anHighlight === "high" ? "≥ 0.7" : "≤ 0.3"} для выбранной метрики`;
    return;
  }
  const sorted = [...pairs].sort((a, b) => (anHighlight === "high" ? b.v - a.v : a.v - b.v));
  const list = sorted.slice(0, 8).map((p) => `${syms[p.i]}↔${syms[p.j]} ${p.v.toFixed(2)}`);
  el.textContent = `Подсвечено пар: ${pairs.length}` +
    (pairs.length > 8 ? ` (топ-8): ${list.join(", ")} …` : `: ${list.join(", ")}`);
}

function renderAnalytics() {
  const d = analyticsData;
  if (!d) return;
  const metric = $("an-mode").value === "cov" ? "returns" : $("an-metric").value;
  const mode = $("an-mode").value === "cov" ? "cov" : "corr";
  const syms = d.symbols;
  const z = mode === "cov" ? d.cov.returns : d.corr[metric];
  const el = $("an-heatmap");
  if (!syms.length) {
    el.textContent = "Нет закэшированных данных — нажмите «Загрузить топ-20»";
    $("an-pairs").textContent = "";
    renderAnTable(d);
    renderAnInfo(d);
    return;
  }
  const cells = z.flat().filter((v) => v != null && Number.isFinite(v));
  if (!cells.length) {
    el.textContent = "Недостаточно пересечений данных для выбранной метрики — расширьте диапазон или загрузите топ-20";
    $("an-pairs").textContent = "";
    renderAnTable(d);
    renderAnInfo(d);
    return;
  }
  const pairs = highlightPairs(d, metric);
  $("an-pairs").textContent = "";
  const lo = mode === "corr" ? -1 : Math.min(...cells);
  let hi = mode === "corr" ? 1 : Math.max(...cells);
  if (hi <= lo) hi = lo + 1e-9;
  const annotations = [];
  const match = new Set();
  for (const { i, j } of pairs) { match.add(i + "," + j); match.add(j + "," + i); }
  const dimCells = anHighlight !== "off" && pairs.length > 0;
  for (let i = 0; i < syms.length; i++) {
    for (let j = 0; j < syms.length; j++) {
      const v = z[i][j];
      const bright = !dimCells || match.has(i + "," + j);
      annotations.push({
        x: syms[j], y: syms[i], text: fmtCell(v, mode),
        showarrow: false,
        font: { size: syms.length > 14 ? 8 : 10, color: bright ? "#dbe2f0" : "#5c6577" },
      });
    }
  }
  const titleBase = {
    returns: "Корреляция дневных лог-доходностей",
    rv: "Корреляция волатильности (RV 7д, годовая)",
    funding: "Корреляция ставки фандинга",
  }[metric];
  if (!el.classList.contains("js-plotly-plot")) el.textContent = "";
  const heatBase = {
    type: "heatmap",
    colorscale: "RdBu", reversescale: true,
    zmin: lo, zmax: hi,
    xgap: 1, ygap: 1,
    hovertemplate: "%{y} ↔ %{x}<br>%{z:.4f}<extra></extra>",
  };
  const traces = dimCells
    ? [
        { ...heatBase, x: syms, y: syms, z: z.map((row, i) => row.map((v, j) => (match.has(i + "," + j) ? null : v))), opacity: 0.3, showscale: false },
        { ...heatBase, x: syms, y: syms, z: z.map((row, i) => row.map((v, j) => (match.has(i + "," + j) ? v : null))), colorbar: { title: mode === "cov" ? "cov" : "ρ", thickness: 12 } },
      ]
    : [{ ...heatBase, x: syms, y: syms, z, colorbar: { title: mode === "cov" ? "cov" : "ρ", thickness: 12 } }];
  Plotly.react(el, traces, {
    ...PLOT_LAYOUT,
    // heatmap — фиксированная матрица: оси fixedrange не дают ни зума, ни
    // рамки выделения (dragmode при этом остаётся рабочим для plotly_click)
    title: `${titleBase} · топ-20${mode === "cov" ? " · ковариация" : ""}`,
    margin: { l: 70, r: 40, t: 40, b: 90 },
    xaxis: { tickangle: -45, gridcolor: "transparent", fixedrange: true },
    yaxis: { autorange: "reversed", gridcolor: "transparent", fixedrange: true },
    annotations,
    shapes: pairShapes(pairs),
  }, PLOT_CFG);
  updatePairsInfo(pairs, syms);
  if (!el.dataset.clickWired) {
    el.dataset.clickWired = "1";
    el.on("plotly_click", (ev) => {
      const pt = ev.points && ev.points[0];
      if (!pt || pt.x === pt.y) return;
      overlayFromAnalytics(pt.y, pt.x);
    });
  }
  renderAnTable(d);
  renderAnInfo(d);
}

function renderAnInfo(d) {
  const info = $("an-info");
  const miss = d.missing || [];
  info.textContent = miss.length
    ? `Без данных в кэше (${miss.length}): ${miss.join(", ")}`
    : `Все ${d.symbols.length} монет топ-20 в кэше`;
}

function renderAnTable(d) {
  const tbl = $("an-table");
  const fmt = (v, suf = "") => (v == null ? "—" : v.toFixed(2) + suf);
  const rows = d.stats.map((s) => `<tr>
      <td>${s.symbol}${d.base === s.symbol ? " ★" : ""}</td>
      <td>${s.days}</td>
      <td>${s.from_ts ? new Date(s.from_ts * 1000).toISOString().slice(0, 10) : "—"}</td>
      <td>${s.to_ts ? new Date(s.to_ts * 1000).toISOString().slice(0, 10) : "—"}</td>
      <td>${fmt(s.rv_mean, "%")}</td>
      <td>${fmt(s.rv_last, "%")}</td>
      <td>${fmt(s.funding_mean, "%/д")}</td>
      <td>${fmt(s.funding_last, "%/д")}</td>
      <td>${s.beta == null ? "—" : s.beta.toFixed(2)}</td>
    </tr>`).join("");
  tbl.innerHTML = `<thead><tr>
      <th>Монета</th><th>Дней</th><th>С</th><th>По</th>
      <th>RV средн.</th><th>RV последн.</th>
      <th>Фандинг средн.</th><th>Фандинг последн.</th>
      <th>β к ${d.base || "BTC"}</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

function overlayFromAnalytics(baseSym, overlaySym) {
  const sel = $("coin-select");
  if ([...sel.options].some((o) => o.value === baseSym)) sel.value = baseSym;
  overlaySymbols = overlaySym && overlaySym !== baseSym ? [overlaySym] : [];
  setView("dashboard");
  populateOverlaySelect();
  if (currentSymbol !== sel.value) switchSymbol(sel.value);
  else drawDashboard(currentSymbol);
  saveState();
}

async function loadTop20() {
  let coins = [];
  try {
    coins = (await jget("/api/coins")).coins || [];
  } catch (_e) { /* fall through */ }
  if (!coins.length) return;
  const btn = $("an-load-top20");
  btn.disabled = true;
  let i = 0;
  for (const c of coins) {
    i += 1;
    setStatus(`Загрузка топ-20: ${c.symbol} (${i}/${coins.length})`, (i - 1) / coins.length, false);
    try {
      const job = await jget(`/api/refresh/${encodeURIComponent(c.symbol)}`, { method: "POST" });
      if (job.state === "running" && job.id) await waitJobDone(job.id);
    } catch (_e) { /* keep going */ }
  }
  btn.disabled = false;
  setStatus("Готово", 1, false);
  setTimeout(hideStatus, 1500);
  analyticsData = null;
  await loadAnalytics();
}

// ------------------------------------------------------------- wiring

async function switchSymbol(symbol) {
  currentSymbol = symbol;
  populateOverlaySelect();
  await drawDashboard(symbol);
  await refreshSymbol(symbol, false);
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadCoins();
  await loadFetchLimit();
  loadState();
  applyLimitUI();
  applyLayout(defaultLayout);

  const sel = $("coin-select");

  sel.addEventListener("change", () => { switchSymbol(sel.value); saveState(); });

  const onDateChange = debounce(() => {
    if (currentView === "analytics") ensureAnalytics();
    else if (currentSymbol) drawDashboard(currentSymbol);
    saveState();
  }, 150);
  for (const id of ["date-from", "date-to"]) {
    $(id).addEventListener("change", onDateChange);
  }
  document.querySelectorAll("[data-preset]").forEach((b) =>
    b.addEventListener("click", () => applyPreset(b.dataset.preset)));
  $("fetch-limit").addEventListener("change", (e) => {
    fetchLimit = Number(e.target.value) || fetchLimit;
    applyLimitUI();
    saveState();
  });
  $("layout-select").addEventListener("change", (e) => {
    applyLayout(e.target.value);
    saveState();
  });
  $("layout-reset").addEventListener("click", () => {
    reorderUnits(DEFAULT_UNIT_ORDER);
    applyLayout(defaultLayout);
    saveState();
  });
  if (window.Sortable) {
    new Sortable(document.querySelector("main"), {
      handle: ".chart-grip",
      animation: 150,
      onEnd: () => { saveState(); applyLayout(defaultLayout); },
    });
  }
  $("refresh-btn").addEventListener("click", () => refreshSymbol(currentSymbol, false));

  $("tab-dashboard").addEventListener("click", () => setView("dashboard"));
  $("tab-analytics").addEventListener("click", () => setView("analytics"));
  $("overlay-coin").addEventListener("change", (e) => {
    const sym = e.target.value;
    if (sym) addOverlay(sym);
  });
  $("overlay-reset").addEventListener("click", resetOverlays);
  $("an-metric").addEventListener("change", () => { saveState(); renderAnalytics(); });
  $("an-mode").addEventListener("change", () => { saveState(); renderAnalytics(); });
  const setHighlight = (mode) => {
    anHighlight = anHighlight === mode ? "off" : mode;
    $("an-hl-high").classList.toggle("active", anHighlight === "high");
    $("an-hl-low").classList.toggle("active", anHighlight === "low");
    renderAnalytics();
  };
  $("an-hl-high").addEventListener("click", () => setHighlight("high"));
  $("an-hl-low").addEventListener("click", () => setHighlight("low"));
  $("an-load-top20").addEventListener("click", loadTop20);

  setView(currentView);

  document.querySelectorAll(".chart-export").forEach((c) =>
    c.addEventListener("change", saveState));

  document.querySelectorAll(".export-btn").forEach((b) =>
    b.addEventListener("click", () => doExport(b.dataset.fmt)));

  switchSymbol(sel.value || "BTC");
});
