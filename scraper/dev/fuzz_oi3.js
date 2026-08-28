#!/usr/bin/env node
/* OI kline fuzz round 3 — many suffixes x interval formats. */
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
  await new Promise(r => setTimeout(r, 5000));
  const res = await page.evaluate(async () => {
    const mod = window.__next_require__(94126);
    let kfn = null;
    for (const k of Object.keys(mod)) { if (typeof mod[k]==='function' && mod[k].toString().includes('"/api/v2/kline"')) { kfn=mod[k]; break; } }
    const out = {};
    const now = Math.floor(Date.now()/1000);
    const sufs = ['aggregated_oi_kline','aggregated_open_interest_kline','aggregated_oi_usd_kline',
      'aggregated_open_interest_usd_kline','aggregated_oi_coin_kline','oi_kline','foi_kline',
      'aggregated_foi_kline','aggregated_liq_kline','aggregated_liquidation_kline',
      'aggregated_cvd_kline','aggregated_spot_cvd_kline','cvd_kline','spot_cvd_kline',
      'buy_sell_qty_kline','buy_sell_usd_kline','aggregated_buy_sell_qty_kline'];
    const bases = ['ALL#BTC#', 'BTC#', 'Binance,OKX,Bybit,Bitget,Hyperliquid#BTC#', 'Binance_BTCUSDT#'];
    for (const b of bases) for (const s of sufs) {
      const sym = b + s;
      try {
        const r = await kfn({ symbol: sym, interval: 'h1', limit: 3, endTime: now, minLimit: false });
        const str = JSON.stringify(r);
        if (r && r.success !== false && r.data && r.data.length) out[sym] = str.slice(0, 180);
      } catch (e) { }
    }
    return out;
  });
  console.log(JSON.stringify(res,null,1));
  await browser.close();
})().catch(e=>{console.error('ERR',e);process.exit(1);});
