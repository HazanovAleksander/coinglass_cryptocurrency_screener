#!/usr/bin/env node
/* Fuzz aggregated OI kline suffix variants. */
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
    let kfn = null;
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      if (mod[k].toString().includes('"/api/v2/kline"')) { kfn = mod[k]; break; }
    }
    const out = {};
    const exs = 'Binance,OKX,Bybit,Bitget,Gate,Hyperliquid,Bitmex,Kraken,HTX,Deribit,Bitfinex,CoinEx,Crypto.com';
    const cands = [
      'ALL#BTC#aggregated_oi_usd_kline',
      'ALL#BTC#aggregated_oi_coin_kline',
      'ALL#BTC#aggregated_open_interest_coin',
      exs + '#BTC#aggregated_open_interest_kline',
      exs + '#BTC#aggregated_oi_kline',
      exs + '#BTC#aggregated_oi_usd',
      exs + '#BTC#aggregated_open_interest_usd',
      exs + '#BTC#aggregated_open_interest',
      'ALL#BTC#aggregated_open_interest',
      'ALL#BTC#open_interest_kline',
      'ALL#BTC#oi_kline',
    ];
    for (const sym of cands) {
      try {
        const r = await kfn({ symbol: sym, interval: '1h', limit: 3, minLimit: false });
        const s = JSON.stringify(r);
        if (r && r.success !== false && !s.includes('"data":null')) out[sym] = s.slice(0, 260);
      } catch (e) { }
    }
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
