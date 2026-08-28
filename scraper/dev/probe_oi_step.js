#!/usr/bin/env node
/* Determine hourly granularity for OI v3 chart. */
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
    let fn = null;
    for (const k of Object.keys(mod)) { if (typeof mod[k]==='function' && mod[k].toString().includes('/api/openInterest/v3/chart')) { fn=mod[k]; break; } }
    const out = {};
    for (const tt of [0,1,2,3,4]) {
      const r = await fn({ symbol:'BTC', timeType:tt, exchangeName:'', currency:'USD', type:0 });
      const dl = r.data.dateList;
      const stepMin = dl.length>1 ? Math.round((dl[1]-dl[0])/60000) : null;
      out['tt'+tt] = { points: dl.length, stepMin, spanDays: Math.round((dl[dl.length-1]-dl[0])/86400000) };
    }
    return out;
  });
  console.log(JSON.stringify(res,null,1));
  await browser.close();
})().catch(e=>{console.error('ERR',e);process.exit(1);});
