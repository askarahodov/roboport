# ROBOport — локальный синтетический стенд (#84)

Это **dev-контур**, не production. Порты БД/MinIO/Keycloak привязаны к loopback. Нельзя загружать реальные заявки, документы или клиентские данные.

## Предварительные требования

Docker Engine + Compose v2, Python 3.13 (venv), Node.js 24, npm, OpenSSL; доступ к OCI-реестрам для образов PostgreSQL 17, Keycloak 26.3.3 и pinned MinIO. Проверка версий и доступности портов 5432/9000/9001/9443 обязательна.

1. `cp infra/dev/local.env.example infra/dev/.env.local`; замените все `CHANGE_TO_...` уникальными локальными паролями. **Не коммитить**; файл считается доверенным shell env, его содержимое исполняется через `source`.
2. `python3.13 -m venv .venv && source .venv/bin/activate`; `python -m pip install -r apps/api/requirements-dev.txt -c apps/api/constraints-py313.txt`.
3. `bash infra/dev/bootstrap-local.sh`. Скрипт делает самоподписанный **временный** TLS для Keycloak, Compose `up -d --wait`, Alembic `upgrade head`, затем создаёт A/B и staff **синтетические** аккаунты.
4. В том же shell доступны переменные после запуска скрипта **только внутри скрипта**, поэтому для API экспортируйте собственный `ROBOport_DATABASE_URL` (как в скрипте), если нужны приватные дев-маршруты. Для публичного стенда достаточно:
   ```bash
   PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
   ```
5. Во втором терминале `cd apps/web && npm ci && npm run dev`. Откройте `http://127.0.0.1:5173`.

`docker compose --env-file infra/dev/.env.local -f infra/dev/compose.local.yml ps` показывает инфраструктуру. Базовая проба: `curl -fsS http://127.0.0.1:8000/health/live`.

## Важное ограничение

Это **единое развёртывание инфраструктуры плюс host-run API/Web**, а не готовый контейнерный стек приложений. Текущие защиты dev HTTP ограничены localhost HTTPS и локальной PostgreSQL; запуск API внутри обычной bridge-сети Docker без перепроектирования security guards был бы небезопасной подменой этих ограничений.

Для полного OIDC входа служит воспроизводимый и **реально испытанный** `.github/workflows/oidc-keycloak.yml`: там API под HTTPS, два пользователя Keycloak и проверка BOLA/revoke в PostgreSQL. Простой HTTP Vite не считается полноценной проверкой приватного интерфейса или OIDC. Последующий checkpoint — единый HTTPS reverse proxy для UI+API и сквозная локальная QA #76.

### Очистка

`docker compose --env-file infra/dev/.env.local -f infra/dev/compose.local.yml down` остановит службы. `down -v` **уничтожит** синтетическую PostgreSQL и MinIO информацию. TLS-файлы и `.env.local` удаляются вручную. Никогда не выполнять `down -v` для ценного окружения.
