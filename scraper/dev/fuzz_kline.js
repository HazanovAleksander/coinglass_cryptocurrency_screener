#!/usr/bin/env node
/* Fuzz /api/v2/kline params in-page to discover valid index enums + symbol formats. */
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
    let klineFn = null;
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const s = mod[k].toString();
      if (s.includes('"/api/v2/kline"')) { klineFn = mod[k]; break; }
    }
    if (!klineFn) return { err: 'no kline fn' };
    const out = {};
    const indexes = ['price', 'cvd', 'spot_cvd', 'futures_cvd', 'vol', 'volume', 'oi', 'open_interest', 'aggregated_cvd', 'cvd_kline', 'spot_price', 'taker_buy_sell'];
    for (const idx of indexes) {
      try {
        const r = await klineFn({ symbol: 'BTC', interval: 'h1', limit: 3, index: idx });
        out[idx] = JSON.stringify(r).slice(0, 300);
      } catch (e) { out[idx] = 'EXC ' + String(e).slice(0, 100); }
    }
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
