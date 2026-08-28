# CoinGlass Dashboard

Веб-дашборд по криптовалютам (топ-20 по капитализации) на данных CoinGlass.
Дашборд работает на дневном таймфрейме (**1d**); диапазон дат графиков
задаётся полями «с / по» (максимум истории ограничен лимитом CoinGlass).
Данные добываются in-page скрейпером (headless Chromium +
`__next_require__`, см. skill `scraping-encrypted-web-apis`) — API CoinGlass
шифрует ответы, поэтому расшифровку выполняет собственный клиент страницы.

## Графики

Все ряды рисуются на дневном таймфрейме (1d); поля «с / по» в шапке
сужают окно просмотра без повторной загрузки данных.

- OI-Weighted Funding Rate (OHLC funding + цена)
- Open Interest по всем биржам (stacked area, ~24 биржи; hourly + daily история)
- Volume Spot (buy/sell, USD)
- Volume Futures (buy/sell, USD)
- CVD Spot (кумулятивная дельта buy-sell)
- CVD Futures
- Котировки спот (свечи)

## Архитектура

- `scraper/fetch_bundle.js` — Node + puppeteer-core: открывает
  `coinglass.com/currencies/<SYM>`, вызывает API-модуль 94126 страницы,
  получает уже расшифрованные ряды, пишет один JSON-бандл.
  Прогресс отдаёт строками `PROGRESS i n stage` в stderr.
- `app/` — FastAPI: запускает скрейпер в фоне, нормализует бандл,
  кэширует точки в SQLite (`data/sqlite/coinglass_cache.db`),
  отдаёт JSON для фронтенда и экспорт.
- `app/static/` — фронтенд без сборки: Plotly + status bar загрузки.

### Использованные endpoint'ы CoinGlass (in-page)

| Ряд | endpoint | params |
|---|---|---|
| funding | `/api/priceAndIndicator` | `symbol, interval=h1, index=avg_fr_kline` |
| OI агрегат | `/api/v2/kline` | `symbol=Binance_<SYM>USDT#aggregated_oi_kline, interval=h1` |
| OI по биржам | `/api/openInterest/v3/chart` | `timeType=2` (1h/31 точка), `timeType=0` (daily/вся история) |
| Volume futures | `/api/v2/kline` | `symbol=ALL#<SYM>#aggregated_buy_sell_usd, interval=1h` |
| Volume spot | `/api/v2/kline` | `symbol=ALL#<SYM>#aggregated_spot_buy_sell_usd, interval=1h` |
| Котировки спот | `/api/price` | `symbol, interval=1h` |
| Топ-20 | `/api/spot/marketCap/data` | `sort=marketCap, order=desc, pageSize=20` |

CVD не хранится у CoinGlass отдельным рядом для агрегата — считается на
нашей стороне как кумулятивная сумма (buy − sell).

Скрейпер вызывает kline-эндпоинты с фиксированным `interval` (`1h`/`h1`) и
складывает результат в кэш как дневные ряды (суффикс `_d1`); спот-цены
дополнительно агрегируются в дневные OHLCV-свечи. OI по биржам всегда тянется
и hourly (`timeType=2`), и daily (`timeType=0` — вся история). Дашборд хранит
и отдаёт только дневные данные (d1).

## Запуск

```bash
docker compose up -d dashboard     # http://localhost:8081
docker compose run --rm test       # pytest внутри контейнера
```

Локально (нужен Chrome/Chromium и node):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd scraper && npm install && cd ..
.venv/bin/uvicorn app.main:app --port 8081
```

## Пересборка

```bash
./rebuild.sh                      # тесты → сборка → перезапуск
./rebuild.sh --skip-tests         # быстрый редеплой без тестов
./rebuild.sh --no-cache           # пересборка образа без кеша слоёв
./rebuild.sh --build-only         # собрать, но не поднимать контейнер
```

## Экспорт

Кнопки в шапке дашборда / эндпоинт `GET /api/export/<SYM>.<fmt>`:

- `csv`, `xlsx`, `xls` — скачивание одной сводной таблицы
  (spot OHLCV, funding, OI, объёмы buy/sell, CVD spot/futures).
- `gsheet` — создаёт Google Spreadsheet через OAuth2 web-флоу.
  `GOOGLE_REFRESH_TOKEN` **не нужен**: первый экспорт открывает страницу
  согласия Google в браузере, после чего креды (с refresh-токеном) хранятся
  в подписанной session-cookie и переиспользуются/обновляются автоматически.
  Нужны только:
  - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (OAuth-клиент «Web application»)
  - в Cloud Console добавить в «Authorized redirect URIs»:
    `http://localhost:8081/api/export/google/callback`
  - опционально `CG_SESSION_SECRET` (подпись session-cookie; по умолчанию dev-значение).
  - если дашборд открыт не через `localhost:8081` (домен, IP, Docker-порт,
    прокси), задайте `CG_GOOGLE_REDIRECT_URI` равным точному зарегистрированному
    URI — иначе Google вернёт `redirect_uri_mismatch`.

## CLI (экспорт всех графиков в CSV)

Данные всех графиков дашборда можно выгрузить из кэша без запуска сервера:

```bash
python -m app BTC                          # CSV в stdout
python -m app BTC -o btc.csv               # в файл
python -m app BTC --format xlsx -o btc.xlsx
python -m app ETH --from 2024-01-01 --to 2024-12-31 --limit 5000
```

Доступно локально (`.venv/bin/python -m app …`) и в Docker:
`docker compose run --rm dashboard python -m app BTC`.

Команда **только читает SQLite-кэш** (`CG_DB_DIR`). Если данных по символу
нет — сначала обновите его через веб-дашборд (`POST /api/refresh/<SYM>`).

Таблица покрывает все графики: базовые колонки веб-экспорта (spot OHLCV,
funding, OI agg, объёмы buy/sell, CVD spot/futures) + `rv_7d` (историческая
волатильность) и `oi_ex_<биржа>` (OI по каждой бирже).

Опции: `--limit` (по умолчанию 5000), `--from`/`--to` (ISO-дата или datetime,
напр. `2024-01-01` или `2024-01-01T12:00:00`; без таймзоны трактуется как UTC),
`--format csv|xlsx|xls` (по умолчанию `csv`).

## Конфигурация (env)

| Переменная | Дефолт | Смысл |
|---|---|---|
| `CG_DB_DIR` | `data/sqlite` | папка SQLite-кэша |
| `CG_FETCH_LIMIT` | `4500` | точек на ряд за один fetch (макс. истории CoinGlass) |
| `CG_STALE_DAYS` | `1` | дней; скрейп запускается только если правый край кэша старее |
| `CG_TOP20_TTL` | `86400` | сек; кэш списка топ-20 |
| `CG_SESSION_SECRET` | dev | подпись session-cookie для Google OAuth |
| `CG_GOOGLE_REDIRECT_URI` | из хоста запроса | точный URI редиректа OAuth2 (фикс `redirect_uri_mismatch` за прокси/доменом) |
| `CHROME_BIN` | автопоиск | путь к Chrome/Chromium |
| `CG_NODE_BIN` | `node` | путь к node |

## API

- `GET  /api/coins` — топ-20 монет
- `POST /api/refresh/{symbol}?force=true&from_ts=…&to_ts=…` — фоновый fetch, возвращает job
- `GET  /api/job/{id}` — прогресс (для status bar)
- `GET  /api/dashboard/{symbol}?from_ts=…&to_ts=…&limit=…` — все ряды из кэша (d1)
- `GET  /api/table/{symbol}?from_ts=…&to_ts=…` — сводная таблица (JSON)
- `GET  /api/export/{symbol}.{csv|xlsx|xls|gsheet}?from_ts=…&to_ts=…`
- `GET  /api/export/google/auth` — старт OAuth2 consent (редирект в Google)
- `GET  /api/export/google/callback` — OAuth2 redirect target

## Разведочные скрипты

`scraper/dev/` — одноразовые скрипты, которыми были найдены endpoint'ы и
грамматика символов (`recon*.js`, `fuzz_*.js`, `probe*.js`). Для работы
приложения не нужны.
