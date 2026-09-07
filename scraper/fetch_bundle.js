#!/usr/bin/env node
/*
 * CoinGlass dashboard bundle fetcher.
 *
 * Opens https://www.coinglass.com/currencies/<SYMBOL> in headless Chrome and
 * invokes the site's own (decrypting) API client via Next.js __next_require__
 * (module 94126). Fetches all series needed by the dashboard in ONE browser
 * session and writes a single JSON bundle.
 *
 * Usage:
 *   node fetch_bundle.js <SYMBOL|_TOP20> <limit> <outfile> [timeframe]
 *   timeframe: d1 (default) — the dashboard is daily-only
 *
 * Progress protocol (stderr, one line per stage):
 *   PROGRESS <stage_index> <stage_count> <stage_name>
 */
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const SYMBOL = (process.argv[2] || 'BTC').toUpperCase();
const LIMIT = parseInt(process.argv[3] || '1000', 10);
const OUTFILE = process.argv[4] || '/dev/stdout';
const TIMEFRAME = (process.argv[5] || 'd1').toLowerCase();
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

function chromePath() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  const candidates = [
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium', '/usr/bin/chromium-browser',
  ];
  for (const c of candidates) if (fs.existsSync(c)) return c;
  return null;
}

function progress(i, n, name) {
  process.stderr.write(`PROGRESS ${i} ${n} ${name}\n`);
}

(async () => {
  const exe = chromePath();
  if (!exe) { console.error('ERROR chrome not found; set CHROME_BIN'); process.exit(1); }

  progress(0, 1, 'browser_start');
  const browser = await puppeteer.launch({
    executablePath: exe,
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });

  try {
    const page = await browser.newPage();
    const pageSym = SYMBOL === '_TOP20' ? 'BTC' : SYMBOL;
    progress(1, 10, 'page_load');
    await page.goto(`https://www.coinglass.com/currencies/${pageSym}`, { waitUntil: 'networkidle2', timeout: 90000 });

    // Resolve API client functions once.
    const apiReady = await page.evaluate(() => {
      const req = window.__next_require__;
      if (!req) return false;
      try { return !!req(94126); } catch (e) { return false; }
    });
    if (!apiReady) throw new Error('__next_require__(94126) unavailable');

    async function call(endpoint, params) {
      return page.evaluate(async (endpoint, params) => {
        const mod = window.__next_require__(94126);
        let fn = null;
        for (const k of Object.keys(mod)) {
          if (typeof mod[k] !== 'function') continue;
          let s; try { s = mod[k].toString(); } catch (e) { continue; }
          const m = s.match(/["'`](\/[a-zA-Z0-9_\-\/\.]{3,120})["'`]/);
          if (m && m[1] === endpoint) { fn = mod[k]; break; }
        }
        if (!fn) return { __err: 'fn not found for ' + endpoint };
        try { return await fn(params); } catch (e) { return { __err: String(e) }; }
      }, endpoint, params);
    }

    // Binance quotes some coins only as 1000-quoted futures pairs
    // (1000PEPEUSDT, 1000SHIBUSDT, …): try the plain USDT pair first, then
    // the 1000-quoted one; for price klines rescale OHLC to per-unit prices
    // (volume is a USD notional and is NOT rescaled; OI klines are already
    // in USD and are not rescaled either). Returns null when both fail.
    async function callBinanceKline(suffix, params, rescale) {
      for (const base of [`Binance_${SYMBOL}USDT`, `Binance_1000${SYMBOL}USDT`]) {
        const r = await call('/api/v2/kline', { ...params, symbol: `${base}${suffix}` });
        if (r && !r.__err && Array.isArray(r.data) && r.data.length) {
          if (rescale && base.startsWith('Binance_1000')) {
            r.data = r.data.map((row) => row.map((v, j) =>
              (j >= 1 && j <= 4) ? Number(v) / 1000 : v));
          }
          return r;
        }
      }
      return null;
    }

    const bundle = { symbol: SYMBOL, fetched_at: Math.floor(Date.now() / 1000), series: {} };

    if (SYMBOL === '_TOP20') {
      progress(2, 10, 'top20');
      const r = await call('/api/spot/marketCap/data', { pageNum: 1, pageSize: 20, sort: 'marketCap', order: 'desc' });
      bundle.series.top20 = r;
      progress(10, 10, 'done');
    } else {
      const stages = [
        ['funding', '/api/priceAndIndicator',
          { symbol: SYMBOL, interval: TIMEFRAME, index: 'avg_fr_kline', limit: LIMIT }],
        ['oi_agg', null, null],
        ['oi_by_exchange_hourly', '/api/openInterest/v3/chart',
          { symbol: SYMBOL, timeType: 2, exchangeName: '', currency: 'USD', type: 0 }],
        ['oi_by_exchange_daily', '/api/openInterest/v3/chart',
          { symbol: SYMBOL, timeType: 0, exchangeName: '', currency: 'USD', type: 0 }],
        ['fut_buysell', '/api/v2/kline',
          { symbol: `ALL#${SYMBOL}#aggregated_buy_sell_usd`, interval: TIMEFRAME, limit: LIMIT, minLimit: false }],
        ['spot_buysell', '/api/v2/kline',
          { symbol: `ALL#${SYMBOL}#aggregated_spot_buy_sell_usd`, interval: TIMEFRAME, limit: LIMIT, minLimit: false }],
        ['spot_price', null, null],
      ];
      const n = stages.length + 2;
      let i = 2;
      for (const [name, ep, params] of stages) {
        progress(i, n, name);
        let r;
        if (name === 'oi_agg') {
          r = await callBinanceKline('#aggregated_oi_kline',
            { interval: TIMEFRAME, limit: LIMIT, minLimit: false }, false);
        } else if (name === 'spot_price') {
          r = await callBinanceKline('#kline',
            { interval: TIMEFRAME, limit: LIMIT, minLimit: false }, true);
        } else {
          r = await call(ep, params);
        }
        // The Binance klines above give years of spot history at the native
        // interval but only exist for coins with a Binance (or 1000-quoted
        // Binance) pair; for the rest fall back to /api/price (capped at
        // ~2000 h1 candles ≈ 83 days).
        if (name === 'spot_price' && (!r || r.__err || !(Array.isArray(r.data) && r.data.length))) {
          r = await call('/api/price', { symbol: SYMBOL, interval: 'h1',
            limit: Math.min(LIMIT, 2000), minLimit: false });
        }
        bundle.series[name] = r;
        i += 1;
      }
      progress(n, n, 'done');
    }

    fs.writeFileSync(OUTFILE, JSON.stringify(bundle));
    console.error('OK wrote ' + OUTFILE);
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('ERROR ' + (e && e.message ? e.message : e)); process.exit(1); });
