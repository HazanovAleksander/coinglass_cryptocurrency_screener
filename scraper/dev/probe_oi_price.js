#!/usr/bin/env node
/* Probe OI v3 chart timeTypes and /api/price shapes. */
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
    // OI chart: timeType variants
    for (const tt of [0, 1, 2, 3, 4, 5]) {
      try {
        const r = await by['/api/openInterest/v3/chart']({ symbol: 'BTC', timeType: tt, exchangeName: '', currency: 'USD', type: 0 });
        if (r && r.data) {
          const d = r.data;
          out['oi_tt' + tt] = {
            keys: Object.keys(d).slice(0, 8),
            exchanges: d.dataMap ? Object.keys(d.dataMap).length : 0,
            points: d.dateList ? d.dateList.length : (d.dataMap ? Object.values(d.dataMap)[0].length : 0),
            firstDate: d.dateList ? d.dateList[0] : null,
            lastDate: d.dateList ? d.dateList[d.dateList.length - 1] : null,
          };
        } else out['oi_tt' + tt] = JSON.stringify(r).slice(0, 120);
      } catch (e) { out['oi_tt' + tt] = 'EXC ' + String(e).slice(0, 80); }
    }
    // /api/price
    try {
      const r = await by['/api/price']({ symbol: 'BTC', interval: '1h', limit: 5, minLimit: false });
      out['price'] = JSON.stringify(r).slice(0, 500);
    } catch (e) { out['price'] = 'EXC ' + e; }
    return out;
  });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
