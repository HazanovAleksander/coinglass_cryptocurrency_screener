#!/usr/bin/env node
/* Dump endpoints from ALL webpack modules on a given CoinGlass page. */
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
const filter = process.argv[3] || '.';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise(r => setTimeout(r, 4000));

  const dump = await page.evaluate((filter) => {
    const req = window.__next_require__;
    if (!req) return { err: 'no next require' };
    const rx = new RegExp(filter, 'i');
    const out = [];
    for (const id of Object.keys(req.m || {})) {
      let mod; try { mod = req(id); } catch (e) { continue; }
      if (!mod || typeof mod !== 'object') continue;
      for (const k of Object.keys(mod)) {
        if (typeof mod[k] !== 'function') continue;
        let src; try { src = mod[k].toString(); } catch (e) { continue; }
        if (src.length > 400) continue;
        const m = src.match(/["'`](\/[a-zA-Z0-9_\-\/\.]{3,120})["'`]/);
        if (m && rx.test(m[1])) out.push({ id, key: k, endpoint: m[1] });
      }
    }
    return out;
  }, filter);

  console.log(JSON.stringify(dump, null, 1));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
