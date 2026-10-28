# Организации и филиалы автомобильного сервиса

**Статус: current-dev database foundation; API/UI/production planned.** Задача [#166](https://github.com/askarahodov/roboport/issues/166). Синтетическая модель, без реальных персональных данных.

## Реализованная часть

Миграция `0033_auto_organization` добавляет `auto_organization` с уникальным кодом и неизменяемой идентичностью, а также nullable `auto_service_location.organization_id`. Существующие синтетические филиалы сохраняются **без привязки**: ни legacy `user_role`, ни `auto_location_role_grant` не означают полномочий в новой организации. Привязка филиала к организации однократная и не может быть отозвана/изменена простой UPDATE. Запрет изменения и удаления организации защищён триггерами PostgreSQL. Отзыв через `disabled_at` необратим.

## Что запрещено считать готовым

Нет production ADMIN/bootstrap, создания организации через API, приглашений, графиков и постов, tenant-scoped customer/WorkOrder/Stock/Finance, UI управления и полноценной межорганизационной авторизации. Nullable `organization_id` — миграционный карантин, **не** допуск к данным; все новые endpoints обязаны deny-by-default для NULL и проверять tenant + location + actor + object. До #119 production запрещён.

## Проверки и откат

`PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_organization.py` на disposable PG17 после Alembic upgrade head. Тесты проверяют отсутствие неявной привязки, FK, попытку повторного назначения, изменение identity и необратимость disable. Downgrade удаляет данные новой схемы; **только для одноразовой тестовой БД**, никогда для production.
