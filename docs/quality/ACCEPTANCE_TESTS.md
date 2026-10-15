# Матрица приёмочных проверок ROBOport

**PASS означает фактический запуск теста** с кодом завершения 0 и воспроизводимым окружением. Не путать синтетические unit/browser tests с интеграционными PG/локальными сессиями/MinIO.

| ID | Слой | Приёмочное поведение |
|---|---|---|
| AC-001 | API | catalog candidate не становится supported, неизвестная модель 404 |
| AC-002 | DB | точный variant SKU+region+revision+station проверяется |
| AC-003 | DB | service capability требует доказуемых согласований разными ролями |
| AC-004 | DB | конкурентные резервы не дают отрицательного остатка |
| AC-005 | FSM | неверный переход 409, Safety hold блокирует работы, QC FAIL блокирует выдачу |
| AC-006 | Estimate | SENT версия immutable, сумма точна, новая цена — новая версия/согласие |
| AC-007 | Security | A не читает ticket/attachment B даже при знании UUID |
| AC-008 | Security | новый владелец B не видит прежние заявки и документы A |
| AC-009 | Security | отозванный subject не обходит запрет через idem replay |
| AC-010 | Local Auth | Argon2id, opaque cookie session, idle/absolute TTL, logout/revoke, CSRF, блокировка перебора |
| AC-011 | Claim | телефон/SN/номер заявки не доказывают владение без proof/review |
| AC-012 | Files | приватный MinIO, backend object guard, audit/quarantine, анонимный GET/LIST запрещён |
| AC-013 | UI | Light/Dark/Auto, исходный C, 390px, keyboard |
| AC-014 | UI | loading/empty/401/403/404/409/Safety hold/revoked по-русски |
| AC-015 | API | 2xx и 4xx схемы Error, 429/500, без PII leakage |
| AC-016 | Replay | одинаковый idem key — один эффект, changed payload 409, два worker/restart |
| AC-017 | DB | миграции upgrade/downgrade/pg_dump/pg_restore на disposable PostgreSQL |
| AC-018 | Event | outbox атомарный, worker retry безопасен |
| AC-019 | CI | реальные pytest/type/lint/build/Playwright и отчёт GitHub run |
| AC-020 | Safety | humanoid/quadruped не попадают в активный ремонтный workflow |

## Обязательный отчёт тестировщика

Для каждого AC: GitHub Issue, среда, точная команда, реальный exit code, PASS/FAIL/SKIP/BLOCKED, ссылка на CI/лог, что остаётся непроверенным. Форма «все тесты прошли» без журнала и разделения mock vs real environment запрещена.

## Проверка старта

`PYTHONPATH=apps/api python -m pytest -q tests/bootstrap` и `python scripts/check_contract.py` — локальные проверки без PostgreSQL/MinIO/реальных сессий. `cd apps/web && npm ci && npm run build` нужно выполнить и подтвердить после lockfile (#10).
