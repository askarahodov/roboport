# ROBOport — клиентский кабинет, локальный HTTPS пилот (#23)

**Только синтетические данные.** Публичная авторизация, сторонний Keycloak и реальные SMS/email отсутствуют. Без opt-in backend закрывает приватные HTTP-маршруты.

## Запуск

Подготовьте одноразовый PostgreSQL 17 на `127.0.0.1` с миграциями:
```bash
python -m pip install -r apps/api/requirements-dev.txt -c apps/api/constraints-py313.txt
(cd apps/web && npm ci --no-audit --no-fund)
ROBOport_DATABASE_URL='postgresql+psycopg://roboport:roboport@127.0.0.1:5432/roboport' \
  python -m alembic -c apps/api/alembic.ini upgrade head
ROBOport_DATABASE_URL='postgresql+psycopg://roboport:roboport@127.0.0.1:5432/roboport' \
  ROBOport_DISPOSABLE_DB_ACK=1 bash scripts/dev_account_https.sh
```

Скрипт отказывает без явного подтверждения disposable DB и DSN на 127.0.0.1. Создаёт временный self-signed ключ в `mktemp`, запускает FastAPI HTTPS :8000 и Vite HTTPS :5173, включает опциональные synthetic API. TLS-ключ не попадает в Git, localStorage или приложение и удаляется при выходе. Для теста откройте `https://127.0.0.1:5173/account/login` и явно подтвердите временный локальный сертификат.

Вход возможен только с существующими синтетическими аккаунтами `app_user + local_credential` из изолированного seed. Публичная регистрация отключена. При неготовой БД или неверной роли возвращается отказ, а не фиктивный профиль.

## Приватные API

`GET /api/v1/auth/me` подтверждает текущую opaque серверную сессию. `POST /api/v1/auth/login` возвращает CSRF в памяти текущей React-сессии; opaque session token находится исключительно в HttpOnly Secure SameSite=Lax cookie. `POST /api/v1/auth/logout` требует Origin и CSRF.

Коллекции `GET /api/v1/dev/account/collections/{robots,tickets,estimates,documents}` проверяют active role CLIENT, cookie/session и active account_customer_link внутри SQL. Предъявление произвольного userId/customerId не поддерживается. Чужие заявки/сметы/вложения, отозванная роль или ownership link не возвращаются.

Документы доступны через `/api/v1/dev/attachments/{attachment_id}/content`; object_key и MinIO credentials не возвращаются браузеру. Сметы можно подтвердить только после `SENT`, только с точной `estimateVersion`, Idempotency-Key и действующим CSRF; серверная проверка ownership обязательна. 409 означает устаревшую версию, не успех.

## Acceptance evidence

- `PYTHONPATH=apps/api python -m pytest -q tests/integration` — реальные сессии A/B, Argon2id login, cookies, logout/CSRF, A/B scope и revoked role/link/session.
- `cd apps/web && npm run typecheck && npm run build && npm run test:e2e` — Light/Dark/Auto, 390px, keyboard, 401/403/404/409, login/approval/logout, network retry, protected content links.
- CI GitHub Actions: PostgreSQL 17 + API + web + Browser E2E, скриншоты и traces публикуются в артефактах.

## Оставшиеся границы выпуска

Зависимые #13 (identity) и #16 (общий shell) остаются формально OPEN до независимого DoD. Настройки и подписки на уведомления пока имеют только информационное состояние, без persistent preference store; настоящее подключение каналов не проводится. Публичный доступ и реальные данные запрещены. PR #65 не следует объявлять production-ready без отдельного privacy/security review.
