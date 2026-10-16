# Синтетический каталог совместимости — #18

## Канонический набор идентичности

Модель допуска задаётся точными связями:

- `catalog_brand` → `catalog_model` → `catalog_variant`;
- `catalog_variant` задаёт `manufacturer_sku`, `region_code`, `hardware_revision` и FK на `catalog_dock`;
- `catalog_dock` отдельно фиксирует `dock_sku`, регион и ревизию станции;
- `catalog_part` хранит конкретный P/N;
- `catalog_evidence` хранит только URL источника и SHA-256 доказательства, а не прикреплённый реальный OEM документ.

`service_capability` определяет допустимость конкретной операции для варианта. `part_compatibility` — отдельное доказательное решение для конкретного P/N + variant. Не выводить совместимость по совпадению названия бренда или SKU-подстроки.

## Допуск и независимое согласование

Новые записи создаются **только candidate/unverified**. Перед публикацией нужны **два разных субъекта** — действующий `ENGINEER` и `MANAGER`, каждому требуется независимый действующий `catalog_grant` по модели и действующая локальная серверная сессия. Запись reviewerUserId в payload запрещена: автор берётся исключительно из сессии. Публикация и два review записываются в одну PostgreSQL транзакцию с аудитом; триггеры дополнительно запрещают прямой INSERT/UPDATE в published без двух положительных review.

Статусы `revoked` необратимы. Доказательства и записи reviewer append-only. Повтор с одним `Idempotency-Key` возвращает результат только после перепроверки живого доступа; повтор с изменённой моделью/P-N/evidence или операцией даёт 409.

## API и ограничения

Общие чтения `/api/v1/catalog/brands`, `/models` и `/models/{modelId}` получают синтетические PostgreSQL записи только в opt-in режиме. Без него возвращают безопасный пустой каталог/404, не требуют real accounts.

Контрактные staff-адаптеры:
- `PATCH /api/v1/staff/variants/{variantId}/capabilities/{operationId}`;
- `POST /api/v1/staff/parts/{partId}/compatibilities`.

Они **не являются публичным production сервисом**. Включение возможно исключительно с `ROBOport_SYNTHETIC_ACCESS_API=1`, HTTPS localhost и disposable PostgreSQL 17. Реальные OEM импорты, подписи, устройства, реальные запчасти и платежи запрещены.

## Интеграция склада #21

Таблица `part_compatibility` принадлежит **этому модулю**. Склад #21 должен ссылаться на `catalog_part` и `part_compatibility` с опубликованным статусом, а не создавать альтернативные `inventory_part` или своё поле `verified`. До принятия каталога PR #56 не объединять с main.

## Проверка

`PYTHONPATH=apps/api python -m pytest -q tests/integration tests/contract` на GitHub Actions disposable PostgreSQL; `alembic upgrade head/downgrade base/upgrade head`; dump/restore. Негативные сценарии: unknown model 404, неверный UUID 422, без доказательства публикация запрещена, одно согласование недостаточно, self-review запрещён, отзыв ACL блокирует replay, несовпадающие регион/ревизия/dock не наследуют допуск.
