# Окружение разработки

## Требования

Linux/macOS/Windows+WSL2; Python 3.13; Node.js 24; npm, Git. Для этапов #11/#13 необходим **одноразовый PostgreSQL 17**; для файлов #28 — локальный приватный **MinIO** через Docker Compose. Не использовать рабочую БД или чужую инфраструктуру.

## Backend

```bash
python -m pip install -r apps/api/requirements-dev.txt
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap
python scripts/check_contract.py
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
```

`/health/live` должен отвечать 200, `/health/ready` намеренно возвращает 503 без обязательной БД/служб. Это не ошибка разработки, а fail-closed поведение.

## Frontend

```bash
cd apps/web
npm ci
npm run typecheck
npm run build
```

Пока `package-lock.json` не создан и не проверен в CI, `npm ci` заблокирован — задача #10. Не указывать build как PASS без действительного запуска.

## Секреты, логи и тесты

`.env.example` содержит заглушки. Нельзя копировать реальные токены в публичную ветку. Запуск только на `localhost` до отдельных решений. Интеграционные тесты локальных учётных записей/ACL и PostgreSQL проводить в изолированном контуре. Для файлов #28 запустить приватный MinIO только на `127.0.0.1`, креды взять из игнорируемого `.env.local`, buckets без публичного доступа. Не считать MinIO поднятым, пока `docker compose` и S3 tests реально не выполнялись. Внешний Keycloak/OIDC отложен.

Подробная модель входа и файлов: [`LOCAL_AUTH_AND_MINIO.md`](../security/LOCAL_AUTH_AND_MINIO.md).
