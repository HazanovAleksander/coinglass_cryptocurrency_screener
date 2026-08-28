#!/usr/bin/env bash
#
# rebuild.sh — одна команда для передеплоя новой версии приложения.
#
# По умолчанию:
#   1. прогоняет тесты (в Docker, без локальной установки пакетов);
#   2. пересобирает образ;
#   3. перезапускает контейнер с обновлённым кодом.
#
# Флаги:
#   --skip-tests   не прогонять тесты (быстрый редеплой только кода)
#   --build-only   собрать образ, но не поднимать контейнер
#   --no-cache     пересобрать образ без кеша слоёв (docker build --no-cache)
#
# Использование:
#   ./rebuild.sh
#   ./rebuild.sh --skip-tests --no-cache
#
set -euo pipefail

cd "$(dirname "$0")"

SKIP_TESTS=0
BUILD_ONLY=0
NO_CACHE=""
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    --build-only) BUILD_ONLY=1 ;;
    --no-cache)   NO_CACHE="--no-cache" ;;
    *) echo "Неизвестный флаг: $arg" >&2; exit 2 ;;
  esac
done

echo "==> Останавливаю работающий контейнер (если есть)…"
docker compose down --remove-orphans >/dev/null 2>&1 || true

if [[ "$SKIP_TESTS" -eq 0 ]]; then
  echo "==> Прогон тестов (в Docker)…"
  docker compose run --rm --build test
  echo "==> Тесты прошли."
fi

echo "==> Пересборка образа…"
docker compose build $NO_CACHE

if [[ "$BUILD_ONLY" -eq 0 ]]; then
  echo "==> Запуск приложения…"
  # -d: отсоединённо; контейнер будет жить после завершения скрипта.
  docker compose up -d dashboard
  echo "==> Готово. Приложение: http://localhost:8081"
  docker compose ps
fi
