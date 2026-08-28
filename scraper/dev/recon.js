#!/usr/bin/env node
/* Recon: open CoinGlass pages, log all capi/fapi API request URLs. */
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

function chromePath() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  for (const c of ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'])
    if (fs.existsSync(c)) return c;
  return null;
}

const PAGES = process.argv.slice(2);
if (!PAGES.length) {
  console.error('usage: node recon.js <url> [url...]');
  process.exit(1);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chromePath(),
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });
  for (const url of PAGES) {
    const page = await browser.newPage();
    const seen = new Set();
    page.on('request', (req) => {
      const u = req.url();
      if (/coinglass\.com\/api|capi\.|fapi\./.test(u) && !seen.has(u.split('?')[0] + '?' + (u.split('?')[1] || '').slice(0, 120))) {
        seen.add(u.split('?')[0] + '?' + (u.split('?')[1] || '').slice(0, 120));
      }
    });
    console.log('=== PAGE', url);
    try {
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
      await new Promise(r => setTimeout(r, 5000));
    } catch (e) { console.log('  (goto err:', e.message + ')'); }
    for (const u of [...seen].sort()) console.log('  ', u);
    await page.close();
  }
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
