#!/usr/bin/env node
/* Dump every endpoint exported by CoinGlass API module 94126 -> endpoints.json */
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

  const dump = await page.evaluate(() => {
    const mod = window.__next_require__(94126);
    const out = [];
    for (const k of Object.keys(mod)) {
      if (typeof mod[k] !== 'function') continue;
      const src = mod[k].toString();
      const m = src.match(/["'`](\/[a-zA-Z0-9_\-\/\.]{3,120})["'`]/);
      out.push({ key: k, endpoint: m ? m[1] : null, src: src.length < 200 ? src : src.slice(0, 200) });
    }
    return out;
  });

  fs.writeFileSync('endpoints.json', JSON.stringify(dump, null, 1));
  console.log('total fns:', dump.length);
  console.log('with endpoint:', dump.filter(d => d.endpoint).length);
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
