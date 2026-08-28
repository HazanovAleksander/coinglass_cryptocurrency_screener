#!/usr/bin/env node
/* Search all webpack module sources for a substring; print module ids + context. */
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
function chromePath() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  for (const c of ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'])
    if (fs.existsSync(c)) return c;
  return null;
}
const url = process.argv[2];
const needle = process.argv[3];
const ctx = parseInt(process.argv[4] || '120', 10);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise(r => setTimeout(r, 4000));

  const res = await page.evaluate((needle, ctx) => {
    const req = window.__next_require__;
    if (!req || !req.m) return { err: 'no req.m' };
    const out = [];
    for (const id of Object.keys(req.m)) {
      let src;
      try { src = req.m[id].toString(); } catch (e) { continue; }
      let idx = src.indexOf(needle);
      let hits = [];
      while (idx !== -1 && hits.length < 5) {
        hits.push(src.slice(Math.max(0, idx - ctx), idx + needle.length + ctx));
        idx = src.indexOf(needle, idx + 1);
      }
      if (hits.length) out.push({ id, hits });
    }
    return out;
  }, needle, ctx);

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
