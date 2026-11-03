# Восстановление связанного автомобильного цикла на PG17 (current-dev, #119)

**Только synthetic CI, никакого production и реальных ПДн.** Этот контрольный пункт доказывает, что полный PostgreSQL `pg_dump -Fc` → `pg_restore` сохраняет *конкретный искусственный автомобильный процесс*. Он не является проверкой восстановления реальной базы, подписанным RPO/RTO, юридическим разрешением или самостоятельной процедурой cutover.

## Что именно проверяется

Обязательная CI-job `postgres` сначала выполняет миграции и все PostgreSQL-интеграционные тесты, затем запускает `scripts/auto_restore_lifecycle_probe.py` в режиме `seed`. С помощью тех же авторизованных synthetic-функций, что используются в API, он создаёт отдельную фиктивную организацию/филиал, проверенную связку автомобиля с клиентом, рабочего поста и сотрудника с **`INTAKE` и `WAREHOUSE`**. В одной транзакции записывается будущая Appointment/WorkOrder, одна услуга, две отдельные позиции `PART` и `CONSUMABLE` с физическими stock `RESERVE`. После этого выполняются один синтетический финансовый `CHARGE` (это **не** платёж и не фискальная операция), один перенос Appointment с immutable historical receipt, `ISSUE` одной единицы из зарезервированного SKU и оформленный `RETURN` через роль WAREHOUSE. Отправка клиенту ничего не доставляет, в outbox сохраняются только два dev-события: `CREATED` и `RESCHEDULED`.

Перед dump скрипт читает **фактические строки** из первоначальной базы, связывает Appointment с исходной и текущей бронью, WorkOrder, verified VehicleLink, catalog service, material classification, ledger `RESERVE / ISSUE / RETURN`, физическими остатками (после ISSUE 1 и RETURN 1 по товару: on_hand=8, reserved=2; RETURN не восстанавливает резерв), синтетической финансовой проводкой, балансом, immutable event history и outbox. Он строго проверяет наличие и количество взаимосвязанных строк (в том числе два материала/одну услугу, четыре WorkOrder stock-event, шесть SKU stock-event, одну финансовую проводку, одно первоначальное событие Appointment, две outbox-записи) и соответствие организация/филиал/резерв. **SHA-256 канонического графа** сохраняется в приватном manifest-файле `/tmp/roboport-auto-lifecycle-restore.json` с правами `0600`, **вне** дампа и репозитория; в логи не выводятся VIN, пользователи, токены, идентификаторы, суммы реальных клиентов или содержимое графа.

Затем существующая CI-команда выполняет настоящий `pg_dump -Fc` всей source-DB `roboport` и `pg_restore --exit-on-error` в **отдельную** `roboport_restored`. Сначала проверяется сохранность исходной БД; на восстановленной копии заново читается тот же граф и сверяется с SHA-256, вычисленным **до** dump, включая историю переноса, остатки и outbox. Простая проверка количества таблиц или успешное завершение `pg_restore` недостаточны.

Последний этап запускает **отрицательные проверки только в восстановленной копии**: UPDATE классификации материалов; UPDATE stock-event; изменение неизменяемой истории переноса; отмена брони при незакрытом резерве; второй INSERT брони на уже занятый пост; вставка stock-event в чужую организацию. Все должны быть отвергнуты работающими PostgreSQL-триггерами, exclusion-constraint и tenant-scope. Каждый отрицательный запрос помещён в savepoint и откатывается; после тестов повторно вычисляется SHA графа для доказательства отсутствия случайных изменений. Исходная БД никогда не используется для провокационных мутаций.

## Команды CI (только localhost PG17)

```bash
# В окружении тестового CI после миграций и установки Python deps:
export PYTHONPATH="apps/api:tests/integration"
python -m scripts.auto_restore_lifecycle_probe seed \
  --manifest /tmp/roboport-auto-lifecycle-restore.json

# После pg_dump -Fc и pg_restore в независимую roboport_restored:
python -m scripts.auto_restore_lifecycle_probe verify \
  --manifest /tmp/roboport-auto-lifecycle-restore.json \
  --database roboport
python -m scripts.auto_restore_lifecycle_probe verify-clone \
  --manifest /tmp/roboport-auto-lifecycle-restore.json \
  --database roboport_restored
```

`ROBOport_DATABASE_URL` используется **только** если указывает на `postgresql://roboport:...@127.0.0.1:5432/roboport` (или `postgresql+psycopg` аналог). Query/fragment и все libpq-переопределения запрещены; `conninfo_to_dict` сверяет эффективные поля, после чего новое `make_conninfo` принудительно закрепляет `host=hostaddr=127.0.0.1`, порт 5432 и точный `dbname`. Переменные `PGHOST`, `PGHOSTADDR`, `PGDATABASE`, `PGPORT`, `PGSERVICE`, `PGOPTIONS` и остальные влияющие PG* запрещены. Это исключает перенаправление отрицательных проверок на другую базу через URI query или окружение. Сценарий не умеет работать на внешнем/production-хосте, произвольном порту/имени БД, менять origin или перезаписывать существующий manifest. `verify-clone` запрещён для source; псевдодоступа через браузер или HTTP нет. `SYNTHETIC_AUTO_RESTORE_SEED_OK` и `SYNTHETIC_AUTO_RESTORE_VERIFY_OK` означают только, что синтетическая проверка пройдена. При ошибке — код возврата 1, `SYNTHETIC_AUTO_RESTORE_BLOCKED:<тип>`, CI падает; реального восстановления и разрешения запуска нет.

Тесты: `tests/contract/test_auto_restore_lifecycle_probe.py` (digest, manifest, fail-closed DSN, source/clone mutation boundary) и обязательный шаг `postgres` в `.github/workflows/ci.yml` (seed, настоящая резервная копия, compare original + restored, шесть отказов на восстановленной копии). Скрипт использует существующие интеграционные фикстуры из `tests/integration/` исключительно в CI; **не** устанавливать его как эксплуатационный сервис.

## Откат, диагностика и release gate

Если restore или проверки не прошли, остановите synthetic pipeline; сохраните только разрешённые CI-логи без manifest и персональных данных. Проверьте причины на disposable БД, исправьте migration/trigger или snapshot, затем заново создайте чистую базу и повторите `alembic upgrade`, `pg_dump` и `pg_restore`. **Не** отключайте триггеры, не меняйте историю UPDATE/DELETE и не пропускайте эти тесты ради зелёного CI. При наличии append-only событий Alembic downgrade ряда автомобильных миграций защищён fail-closed; для восстановления нужен изолированный, документированный backup/restore, а не принудительный `downgrade base` с рабочими записями.

Для настоящего запуска дополнительно и независимо обязательны: полное моделирование клиентских/ремонтных/лицензионных данных, согласия/право на историю, верификация backup encryption/retention, замер подтверждённых RPO/RTO, dry-run переноса/cutover с восстановлением доступа, кросс-tenant аудит и подтверждения ответственных security/legal/operations. **`#119`, `#165` и `#172` остаются открытыми**; даже `5/5 GREEN` на этой CI-job не включает production.
