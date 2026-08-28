#!/usr/bin/env node
/* Fuzz /api/v2/kline symbol suffixes (ALL#BTC#<suffix>) in-page. */
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
  await page.goto('https://www.coinglass.com/volume/BTC', { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise(r => setTimeout(r, 3000));

  const res = await page.evaluate(async () => {
    const req = window.__next_require__;
    const mod = req(94126);
    let fn = null; // fapi v2/kline
    let fnC = null; // capi v2/kline
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const s = mod[k].toString();
      if (s.includes('fapi.coinglass.com/api/v2/kline')) { fn = fn || mod[k]; }
      if (s.includes('"/api/v2/kline"')) fnC = fnC || mod[k];
    }
    const out = {};
    const suffixes = [
      'aggregated_spot_buy_sell_usd', 'aggregated_buy_sell_usd',
      'aggregated_cvd', 'aggregated_spot_cvd', 'cvd', 'spot_cvd',
      'aggregated_cvd_usd', 'aggregated_spot_cvd_usd',
      'aggregated_open_interest', 'aggregated_open_interest_usd',
      'aggregated_vol_usd', 'aggregated_spot_vol_usd', 'kline', 'spot_kline',
    ];
    for (const suf of suffixes) {
      const sym = 'ALL#BTC#' + suf;
      for (const [name, f] of [['fapi', fn], ['capi', fnC]]) {
        if (!f) continue;
        try {
          const r = await f({ symbol: sym, interval: '1h', limit: 3, minLimit: false });
          const s = JSON.stringify(r);
          if (r && r.success !== false && !s.includes('"data":null') && !s.includes('klineIndexEnum')) {
            out[name + '|' + suf] = s.slice(0, 400);
          }
        } catch (e) { /* skip */ }
      }
    }
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
