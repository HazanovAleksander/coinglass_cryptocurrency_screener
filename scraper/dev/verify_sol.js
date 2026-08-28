#!/usr/bin/env node
/* Verify symbol grammar generalizes to another coin (SOL). */
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
  const browser = await puppeteer.launch({ executablePath: chromePath(), headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`] });
  const page = await browser.newPage();
  await page.goto('https://www.coinglass.com/currencies/SOL', { waitUntil: 'networkidle2', timeout: 60000 });
  const res = await page.evaluate(async () => {
    const mod = window.__next_require__(94126);
    const by = {};
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const m = mod[k].toString().match(/["'`](\/[a-zA-Z0-9_\-\/\.]{3,120})["'`]/);
      if (m && !by[m[1]]) by[m[1]] = mod[k];
    }
    const out = {};
    async function t(name, fn, params) {
      try {
        const r = await fn(params);
        const s = JSON.stringify(r);
        out[name] = (r && r.success !== false && r.data) ? 'OK ' + s.slice(0, 140) : 'FAIL ' + s.slice(0, 100);
      } catch (e) { out[name] = 'EXC ' + String(e).slice(0, 80); }
    }
    const kfn = by['/api/v2/kline'];
    await t('oi_agg_binance_base', kfn, { symbol: 'Binance_SOLUSDT#aggregated_oi_kline', interval: 'h1', limit: 3, minLimit: false });
    await t('oi_agg_all_base', kfn, { symbol: 'ALL#SOL#aggregated_oi_kline', interval: 'h1', limit: 3, minLimit: false });
    await t('fut_vol', kfn, { symbol: 'ALL#SOL#aggregated_buy_sell_usd', interval: '1h', limit: 3, minLimit: false });
    await t('spot_vol', kfn, { symbol: 'ALL#SOL#aggregated_spot_buy_sell_usd', interval: '1h', limit: 3, minLimit: false });
    await t('funding', by['/api/priceAndIndicator'], { symbol: 'SOL', interval: 'h1', index: 'avg_fr_kline', limit: 3 });
    await t('price', by['/api/price'], { symbol: 'SOL', interval: '1h', limit: 3, minLimit: false });
    // market cap top list sorted
    await t('mcap_sorted', by['/api/spot/marketCap/data'], { pageNum: 1, pageSize: 20, sort: 'marketCap', order: 'desc' });
    return out;
  });
  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
