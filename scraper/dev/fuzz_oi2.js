#!/usr/bin/env node
/* Fuzz aggregated OI kline variants, round 2. */
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
  await page.goto('https://www.coinglass.com/currencies/BTC', { waitUntil: 'networkidle2', timeout: 60000 });
  const res = await page.evaluate(async () => {
    const mod = window.__next_require__(94126);
    let kfn = null;
    for (const k of Object.keys(mod)) { if (typeof mod[k]==='function' && mod[k].toString().includes('"/api/v2/kline"')) { kfn=mod[k]; break; } }
    const out = {};
    const cands = [
      ['BTC#aggregated_open_interest_kline','h1'],
      ['BTC#aggregated_oi_kline','h1'],
      ['ALL#BTC#aggregated_open_interest_kline','h1'],
      ['ALL#BTC#aggregated_oi_kline','h1'],
      ['ALL#BTC#aggregated_oi_usd_kline','h1'],
      ['ALL#BTC#aggregated_open_interest_usd_kline','h1'],
      ['ALL#BTC#aggregated_open_interest_kline','1h'],
      ['ALL#BTC#aggregated_funding_kline','h1'],
      ['ALL#BTC#aggregated_liq_kline','h1'],
      ['ALL#BTC#aggregated_buy_sell_coin','h1'],
      ['ALL#BTC#aggregated_spot_buy_sell_coin','h1'],
    ];
    for (const [sym, iv] of cands) {
      try {
        const r = await kfn({ symbol: sym, interval: iv, limit: 3, minLimit: false });
        const s = JSON.stringify(r);
        if (r && r.success !== false && !s.includes('"data":null')) out[sym+'|'+iv] = s.slice(0, 220);
      } catch (e) { }
    }
    return out;
  });
  console.log(JSON.stringify(res,null,1));
  await browser.close();
})().catch(e=>{console.error('ERR',e);process.exit(1);});
