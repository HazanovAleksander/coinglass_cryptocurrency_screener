#!/usr/bin/env node
/* Final probe: OI kline suffixes + top coins by market cap. */
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
    const by = {};
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const s = mod[k].toString();
      const m = s.match(/["'`](\/[a-zA-Z0-9_\-\/\.]{3,120})["'`]/);
      if (m && !by[m[1]]) by[m[1]] = mod[k];
    }
    const out = {};
    const kfn = by['/api/v2/kline'];
    for (const sym of [
      'ALL#BTC#aggregated_open_interest_kline',
      'ALL#BTC#aggregated_oi_kline',
      'ALL#BTC#aggregated_oi_usd',
      'ALL#BTC#aggregated_open_interest_usd_kline',
      'Binance_BTCUSDT#open_interest_kline',
      'Binance_BTCUSDT#oi_kline',
    ]) {
      try {
        const r = await kfn({ symbol: sym, interval: '1h', limit: 3, minLimit: false });
        const s = JSON.stringify(r);
        if (r && r.success !== false && !s.includes('"data":null')) out[sym] = s.slice(0, 300);
      } catch (e) { }
    }
    // top coins by market cap
    for (const ep of ['/api/spot/marketCap/data', '/api/home/coinMarkets', '/api/futures/v2/coins/markets', '/api/coin/tickers']) {
      try {
        const r = await by[ep]({ pageNum: 1, pageSize: 25, sort: '', order: '' });
        out['EP ' + ep] = JSON.stringify(r).slice(0, 600);
      } catch (e) { out['EP ' + ep] = 'EXC ' + String(e).slice(0, 80); }
    }
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
