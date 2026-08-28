#!/usr/bin/env node
/* Probe: load a CoinGlass page, walk webpack modules, find API client fns
 * whose source matches given endpoint substrings, and try calling them. */
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
function chromePath() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  for (const c of ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'])
    if (fs.existsSync(c)) return c;
  return null;
}

const url = process.argv[2] || 'https://www.coinglass.com/currencies/BTC';
const patterns = process.argv.slice(3);
if (!patterns.length) patterns.push('priceAndIndicator', 'openInterest/v3/chart', 'cvd', 'spot', 'kline');

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise(r => setTimeout(r, 3000));

  const found = await page.evaluate(async (patterns) => {
    const req = window.__next_require__;
    if (!req) return { err: 'no __next_require__' };
    const ids = Object.keys(req.m || {});
    const hits = [];
    for (const id of ids) {
      let mod;
      try { mod = req(id); } catch (e) { continue; }
      if (!mod || typeof mod !== 'object') continue;
      for (const k of Object.keys(mod)) {
        let src;
        try { src = mod[k].toString(); } catch (e) { continue; }
        if (typeof mod[k] !== 'function') continue;
        for (const p of patterns) {
          if (src.includes(p)) {
            const m = src.match(/["'`](\/?api[^"'`]{0,120})["'`]/g) || [];
            hits.push({ id, key: k, pattern: p, endpoints: [...new Set(m)].slice(0, 8), len: src.length });
            break;
          }
        }
      }
    }
    return { moduleCount: ids.length, hits };
  }, patterns);

  console.log(JSON.stringify(found, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
