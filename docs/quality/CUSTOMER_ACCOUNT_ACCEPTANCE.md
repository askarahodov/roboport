# Личный кабинет ROBOport — приёмка #23

**Только синтетические данные, HTTPS loopback, закрытая саморегистрация.** Не использовать реальные аккаунты, платежи, Keycloak и прямой browser MinIO SDK.

## Шесть разделов

- `/account` — обзор и статус локальной серверной сессии.
- `/account/robots` — только роботы в пределах активной связи аккаунта с customer.
- `/account/tickets` — список собственных ticket с серверным ограничением количества и порядком.
- `/account/estimates` — только версии `SENT/APPROVED/DECLINED`, сумма в minor units. Клиентское решение идёт через HTTPS backend с CSRF, Idempotency-Key, точной версией и серверной проверкой владения.
- `/account/documents` — только `READY + CLEAR` attachment metadata; содержимое отдаёт отдельный защищённый backend, `object_key` и MinIO credentials браузеру не передаются.
- `/account/settings` — только собственная настройка внутренних уведомлений, запись с CSRF, аудит изменений. Никаких реальных SMS/email.

## Безопасность

Identity устанавливается только из live opaque session cookie `rp_session` (HttpOnly/Secure/SameSite); поля `X-User-ID/X-Customer-ID` игнорируются. Активный CLIENT grant и связь account_customer_link проверяются SQL сессией; после revoke нет доступа, чужие ticket/estimate/attachment UUID не раскрывают содержимое.

`POST /auth/csrf` — защищённое loopback HTTPS POST с обязательным совпадением Origin. Введена отдельная таблица `user_session_csrf_rotation`, **исходные поля user_session не меняются**; после ротации старый CSRF недействителен. Logout отменяет живую сессию. Frontend не сохраняет cookie, csrf, пароль и токены в browser storage; localStorage используется только для темы.

## Исполняемая проверка

- `PYTHONPATH=apps/api python -m pytest -q tests/integration` в disposable PostgreSQL 17: две настоящие user_session A/B, ownership revoke, session revoke, forged identity headers, список своих документов и SENT-смет, row-level prefs, CSRF denied, rotated CSRF, logout и no audit duplicates.
- `cd apps/web && npm ci && npm run typecheck && npm run build && npm run test:e2e`: Chromium, 390px, Light/Dark/Auto, keyboard/focus, network/401/403, отсутствие localStorage auth, шесть приватных маршрутов, скриншоты.
- GitHub Actions дополнительно исполняет Alembic `0018_customer_preferences`: upgrade/downgrade/upgrade, pg_dump/restore и API OpenAPI contract; проверяет канонический SVG C и WCAG AA токены.

**Разделение evidence:** Browser UI сценарий авторизованного вида может использовать сетевые ответы для проверки отображения, но security/ownership DoD подтверждается реальными PostgreSQL+HTTP тестами, а не таким mock. Публичное развёртывание, реальные контакты/OEM, production security assessment и юридическая сила согласия — отдельные release gates.
