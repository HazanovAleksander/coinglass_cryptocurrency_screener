#!/usr/bin/env node
/* Recon v2: log full request URLs (incl. XHR/fetch/WS) on a page, wait longer. */
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
const waitMs = parseInt(process.argv[3] || '10000', 10);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', `--user-agent=${UA}`],
  });
  const page = await browser.newPage();
  page.on('request', (req) => {
    const u = req.url();
    if (/capi\.|fapi\.|coinglass\.com\/api|wss:/.test(u)) console.log('REQ', req.resourceType(), u);
  });
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise(r => setTimeout(r, waitMs));
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
