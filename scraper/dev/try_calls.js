#!/usr/bin/env node
/* Try calling in-page API fns with candidate params; print result shape. */
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
function chromePath() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  for (const c of ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'])
    if (fs.existsSync(c)) return c;
  return null;
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });
  const page = await browser.newPage();
  await page.goto('https://www.coinglass.com/currencies/BTC', { waitUntil: 'networkidle2', timeout: 60000 });

  const res = await page.evaluate(async () => {
    const mod = window.__next_require__(94126);
    const byEndpoint = {};
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const src = mod[k].toString();
      const m = src.match(/["'`](\/[a-zA-Z0-9_\-\/\.]{3,120})["'`]/);
      if (m) byEndpoint[m[1]] = mod[k];
    }
    const out = {};
    async function tryCall(name, ep, params) {
      const fn = byEndpoint[ep];
      if (!fn) { out[name] = { err: 'no fn ' + ep }; return; }
      try {
        const r = await fn(params);
        let sample = r;
        const s = JSON.stringify(r);
        out[name] = { ok: true, keys: r && typeof r === 'object' ? Object.keys(r).slice(0, 10) : null, snippet: s ? s.slice(0, 700) : String(r) };
      } catch (e) { out[name] = { err: String(e).slice(0, 200) }; }
    }
    await tryCall('oi_chart', '/api/openInterest/v3/chart', { symbol: 'BTC', timeType: 2, exchangeName: '', currency: 'USD', type: 0 });
    await tryCall('kline_v2_fut', '/api/v2/kline', { symbol: 'Binance_BTCUSDT', interval: 'h1', limit: 10, endTime: Math.floor(Date.now()/1000) });
    await tryCall('kline_v2_cvd', '/api/v2/kline', { symbol: 'BTC_AGGREGATED_CVD', interval: 'h1', limit: 10 });
    await tryCall('spot_agg_kline', '/api/kline', { symbol: 'BTC', interval: 'h1', limit: 10 });
    await tryCall('vol_chart', '/api/futures/vol/chart', { symbol: 'BTC', timeType: 2, exchangeName: '', currency: 'USD' });
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
