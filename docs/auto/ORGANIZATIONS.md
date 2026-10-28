# Организации и филиалы автомобильного сервиса

**Статус: current-dev database foundation; API/UI/production planned.** Задача [#166](https://github.com/askarahodov/roboport/issues/166). Синтетическая модель, без реальных персональных данных.

## Реализованная часть

Миграция `0033_auto_organization` добавляет `auto_organization` с уникальным кодом и неизменяемой идентичностью, а также nullable `auto_service_location.organization_id`. Существующие синтетические филиалы сохраняются **без привязки**: ни legacy `user_role`, ни `auto_location_role_grant` не означают полномочий в новой организации. Привязка филиала к организации однократная и не может быть отозвана/изменена простой UPDATE. Запрет изменения и удаления организации защищён триггерами PostgreSQL. Отзыв через `disabled_at` необратим.

## Что запрещено считать готовым

Нет production ADMIN/bootstrap, создания организации через API, приглашений, графиков и постов, tenant-scoped customer/WorkOrder/Stock/Finance, UI управления и полноценной межорганизационной авторизации. Nullable `organization_id` — миграционный карантин, **не** допуск к данным; все новые endpoints обязаны deny-by-default для NULL и проверять tenant + location + actor + object. До #119 production запрещён.

## Проверки и откат

`PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_organization.py` на disposable PG17 после Alembic upgrade head. Тесты проверяют отсутствие неявной привязки, FK, попытку повторного назначения, изменение identity и необратимость disable. Downgrade удаляет данные новой схемы; **только для одноразовой тестовой БД**, никогда для production.

## Записи полномочий (миграция 0034; только каркас)

Добавлена таблица `auto_organization_role_grant` для штатных ролей `MANAGER`, `INTAKE`, `MECHANIC`, `WAREHOUSE`, с привязкой к организации и необязательному филиалу. База запрещает добавлять назначение к филиалу другой организации, повторять активный grant, менять идентичность записи или отменять отзыв. Связанные организации/филиалы должны быть действующими при создании назначения.

**Это ещё не безопасный процесс выдачи прав:** существующая БД не имеет доверенного API выдачи ролей, actor audit и утверждённого ADMIN bootstrap. Пока новая таблица недоступна из HTTP и не используется в проверках реальных прав; прямые записи допускаются только из закрытых тестовых fixtures. Не выдавать `MANAGER` или `INTAKE` через legacy `user_role` на основании этой схемы. Реальные операции требуют отдельного service-layer с сериализацией отзывов, проверкой сессий и авторизованного администратора, audit и E2E. Задача #166 остаётся открытой.

Проверки: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_org_grants.py` на disposable PG17 после `alembic upgrade head`.
