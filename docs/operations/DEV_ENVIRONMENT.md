# Окружение разработки ROBOport / Airmatik

**Статус:** current для синтетического robot-domain dev стенда; авто-функции пока строятся по этапам. Раздел не даёт разрешения на production. Дата сверки: 26.10.2026.

## Требования

Docker Engine + Compose v2, Python 3.13, Node.js 24, npm и Git. Для полноценного dev требуется одноразовый PostgreSQL 17 и приватный MinIO, при OIDC — Keycloak 26.3.3. Синтетический локальный стенд описан в [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md).

## Быстрые проверки (Linux/macOS с shell)

```bash
python -m pip install -r apps/api/requirements-dev.txt -c apps/api/constraints-py313.txt
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap tests/contract
python scripts/check_contract.py
cd apps/web
npm ci
npm run typecheck
npm run build
```

Эти команды проверяют код/контракты, но PG интеграционные тесты требуют отдельного подготовленного стенда.

## Запуск инфраструктуры dev

Создать приватный `infra/dev/.env.local` на основе `infra/dev/local.env.example`, заменить все `CHANGE_TO_...` уникальными локальными значениями. Затем:

```bash
bash infra/dev/bootstrap-local.sh
docker compose --env-file infra/dev/.env.local -f infra/dev/compose.local.yml ps
```

Bootstrap запускает Compose инфраструктуру PostgreSQL/MinIO/Keycloak, применяет Alembic и подготавливает синтетические аккаунты. Web/API **не** контейнеризованы вместе с этим dev-стендом: они запускаются на host и используют локальные ограничения безопасности. Для корректного единого HTTPS origin использовать [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md), команду `bash infra/dev/start-https.sh` и соответствующие сертификаты только в отдельном тестовом профиле браузера.

## Запуск API без БД (ограниченный public smoke)

```bash
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
```

`/health/live` возвращает 200, `/health/ready` без служб может намеренно возвращать 503. Не обходить проверку HTTPS/loopback и не публиковать этот dev стенд.

## Windows

В Git Bash/WSL2 можно следовать shell-инструкциям выше. При запрещённом PowerShell и наличии только **CMD** для работы с Compose допустима команда:

```cmd
docker compose --env-file infra/dev/.env.local -f infra/dev/compose.local.yml ps
```

Но `bootstrap-local.sh` и `start-https.sh` **не запускаются нативным CMD**. Поэтому простую команду `docker compose up` нельзя выдавать за полный рабочий локальный API/WEB+Keycloak/OIDC; воспроизводимый CMD-only bootstrap ещё необходимо реализовать и проверить по #122/#119. Не обещать успешный native Windows запуск без прогона.

## Тесты и безопасные операции

`apps/web/package-lock.json` присутствует, `npm ci` проверяется CI. Внешние OIDC, локальные сессии, PostgreSQL и MinIO **уже тестируются** на синтетике, но не означают production-readiness. Скрипты миграций нельзя применять к действующей базе без backup/rollback. `docker compose ... down -v` **удаляет данные dev volumes**. Не использовать реальные VIN, телефоны, клиентские обращения или секреты в Git и screenshots.

Подробности: [модель безопасности](../security/LOCAL_AUTH_AND_MINIO.md), [документационный аудит](../auto/DOCUMENTATION_AUDIT.md).

## GitHub Actions — политика параллельных прогонов (#135)

Тяжёлый workflow `.github/workflows/ci.yml` запускается на каждый `pull_request`, при push в `main` и по ручному `workflow_dispatch`. Отдельный `push` feature-ветки **не** создаёт второй идентичный прогон, если PR уже открыт. В рамках одного PR новые изменения отменяют устаревшие CI-прогоны через `concurrency.cancel-in-progress`, но не отменяют проверки других PR или `main`.

Не закрывать PR, пока **последний head SHA** не прошёл PostgreSQL, API, Web и Browser E2E. Установка Playwright Chromium требует сетевого доступа runner к зависимостям; зависание установки браузера — инфраструктурный блокер, а не `PASS`, и нельзя скрывать его отключением browser job. При проблемах проверять шаг `Install browser test runner and Chromium`, GitHub Actions run и публикацию `trace` для ошибок тестов.
