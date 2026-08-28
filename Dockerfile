FROM node:22-bookworm-slim

# Chromium for the CoinGlass in-page scraper + Python for the web app
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium fonts-liberation libnss3 libatk-bridge2.0-0 \
      libgtk-3-0 libgbm1 libasound2 libxshmfence1 \
      python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Node deps (puppeteer-core only; browser comes from apt)
COPY scraper/package.json scraper/package-lock.json* ./scraper/
RUN cd scraper && npm install --omit=dev

# Python deps
COPY requirements.txt ./
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scraper/fetch_bundle.js ./scraper/

ENV CHROME_BIN=/usr/bin/chromium \
    PUPPETEER_SKIP_DOWNLOAD=true \
    CG_SCRAPER_DIR=/srv/scraper \
    CG_DB_DIR=/srv/data/sqlite \
    PATH="/venv/bin:$PATH"

EXPOSE 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
