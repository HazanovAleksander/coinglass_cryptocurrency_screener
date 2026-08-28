#!/usr/bin/env node
/* Fuzz *_kline index enums against /api/v2/kline and /api/kline in-page. */
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
    let v2 = null, v1 = null;
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const s = mod[k].toString();
      if (s.includes('"/api/v2/kline"')) v2 = mod[k];
      if (s.includes('"/api/kline"')) v1 = mod[k];
    }
    const out = { v2: {}, v1: {} };
    const idxs = ['price_kline', 'oi_kline', 'cvd_kline', 'spot_cvd_kline', 'futures_cvd_kline',
      'vol_kline', 'volume_kline', 'taker_kline', 'liq_kline', 'agg_cvd_kline',
      'aggregated_cvd_kline', 'spot_kline', 'futures_kline', 'oi_weight_kline',
      'buy_sell_kline', 'net_flow_kline', 'fr_kline', 'avg_fr_kline'];
    for (const idx of idxs) {
      try {
        const r = await v2({ symbol: 'BTC', interval: 'h1', limit: 2, index: idx });
        const s = JSON.stringify(r);
        if (!s.includes('klineIndexEnum')) out.v2[idx] = s.slice(0, 250);
      } catch (e) { out.v2[idx] = 'EXC'; }
    }
    for (const idx of idxs) {
      try {
        const r = await v1({ symbol: 'Binance_BTCUSDT', interval: 'h1', limit: 2, index: idx });
        const s = JSON.stringify(r);
        if (!s.includes('klineIndexEnum')) out.v1[idx] = s.slice(0, 250);
      } catch (e) { out.v1[idx] = 'EXC'; }
    }
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
