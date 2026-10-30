# Границы разработки и внешнего выпуска

| Gate | Что требуется | Что разрешает |
|---|---|---|
| DEV | Документы, Issues, реально зелёный CI и синтетические тесты | Внутреннюю разработку |
| DB | Исполняемые миграции и восстановление на disposable PG | Проверку бизнес-транзакций в dev |
| AUTH | Настоящие локальные сессии PostgreSQL, RBAC+ACL, CSRF, отзыв доступа и отрицательные BOLA/IDOR тесты | Тестирование приватных API на синтетике |
| FILES | Приватный MinIO в dev, объектные guards API, quarantine и аудит | Синтетические документы на dev стенде |
| DESIGN | Утверждённый handoff, доступность, оба визуальных режима | Разработческий UI и позднее публичный брендинг после IP |
| SECURITY | Независимая проверка безопасности и рисков | Допуск к следующему релизному этапу |
| LEGAL/BIZ | Обработка ПД, бренд/знак, экономика и реальные условия сервиса | Решение о внешнем запуске |

**Ни один отдельный gate не разрешает работу с реальными клиентами.** Тесты frontend на mock не заменяют DB/AUTH/FILES. Юридические и коммерческие вопросы можно выполнять параллельно позже, но они блокируют публичное обслуживание.

## Отдельный gate хранения доказательств автомобиля (#130)

Состояние на 27.10.2026: `scripts/inspect_auto_evidence.py` даёт **только агрегированную read-only инвентаризацию** на disposable synthetic PG17. Без утверждённых юридических сроков, согласий, порядка сохранения `Idempotency-Key` replay, аудиторских обязательств, приватности, резервного копирования и восстановления нельзя добавлять `DELETE`, `DROP`, `TRUNCATE` или автоматический archive для `auto_create_receipt`, `auto_claim_retry_receipt`, `auto_vehicle_claim`, `auto_vehicle_claim_reissue` и связанных append-only audit. Даже при успешном dev CI production gate остаётся **закрыт**. Ручная 24-часовая очистка **только** `auto_proof_throttle` — самостоятельная dev-операция и не разрешает удаление proof/evidence.

## Контроль конкурентного отзыва сессий для auto-операций (#130)

Dev AUTH-gate требует проверки не только `user_role` и `auto_vehicle_staff_assignment`, но и жизненного цикла `user_session`/`app_user` до commit операций `issue-proof`, `reissue-proof`, `redeem`, `review`, `revoke`, создания pending-связи и делегирования INTAKE. PG17 concurrency-тесты: `tests/integration/test_pg_auto_session_revocation.py`. Результат не расширяет права `MANAGER`, не создаёт филиалы и не меняет запрет на реальные персональные данные; production security/privacy approval требует отдельного gate.

## Филиальные права: технический пилот vs production gate (#130)

Миграция `0030_auto_location_scope` вводит технический `auto_service_location`, неизменяемую связь автомобиля с филиалом и журнал текущих/отозванных `auto_location_role_grant`. При **явном synthetic flag `ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE=1`** staff-операции проверяют активную роль, vehicle assignment и grant на один и тот же филиал; пустые/чужие/отозванные полномочия запрещены. SQL-факторы сохраняют блокировки до commit; PG17-тесты — `tests/integration/test_pg_auto_location_scope.py`. При выключенном pilot flag поведение текущей synthetic системы сохраняется, поэтому это **не** обязательная production enforcement-модель.

До реализации защищённого и независимо утверждённого bootstrap владельца организации/филиала, выдачи `MANAGER` по правилу least privilege, перехода legacy-машин к явному филиалу, политики перевода автомобиля между филиалами и независимого security review этот этап не может быть использован для реального запуска. Нет нового публичного API, пользовательских операций с реальными подразделениями, незаметной выдачи прав или доступа к данным клиентов.

## Peer/TLS trust boundary для auto proof — технический gate #159

Внутренний synthetic auto API не принимает `Forwarded`, `X-Forwarded-*` и распространённые альтернативы на входе. Запрет действует до открытия подключения к PostgreSQL; Uvicorn в поставляемом dev Dockerfile явно использует `--no-proxy-headers`. Истинный TLS определяется сервером, а не присланным `X-Forwarded-Proto`. Допускается только явный loopback+HTTPS сценарий, как описано в auto ownership docs. Если текущий Compose/Nginx проксирует HTTP и передаёт `X-Forwarded-*`, соответствующие synthetic auto endpoints должны отказать до отдельного одобренного изменения схемы TLS — не отключать проверку ради зелёных тестов.

PG17 отрицательные и positive control тесты: `tests/integration/test_pg_auto_proxy_boundary.py`; bootstrap/CI запреты: `tests/bootstrap/test_auto_proxy_boundary.py`. Оставшиеся **OPEN**: trusted ingress (явное реальное peer/socket доверие, сертификаты и XFF sanitization), IP limiter across workers, production anti-abuse и решение SECURITY/LEGAL #119. Данный gate — защита от спуфинга transport metadata, а не реализация всех этих пунктов.

## Atomic direct-loopback peer rate limit — dev-пилот #161

Миграция `0031_auto_peer_throttle` добавляет фиксированный **30/900с** счётчик всех `POST /api/v1/dev/auto/*` по HMAC-SHA256 числового `ASGI scope.client`. Используются отдельные короткие транзакции PostgreSQL с `ON CONFLICT`, без хранения raw IP и без доверия к `X-Forwarded-For`; проверка выполняется до CSRF/session, поэтому неверные попытки и retry тоже учитываются. Пилот выключен по умолчанию; включение требует `ROBOport_SYNTHETIC_AUTO_PEER_LIMIT=1`, ключ `ROBOport_SYNTHETIC_AUTO_PEER_HMAC_KEY` минимум 32 байт, синтетический API, loopback HTTPS. Любые иные источники peer, отключённая БД или слабый секрет **fail closed**. Наличие Uvicorn `--no-proxy-headers` (#159) обязательно.

Тесты `tests/bootstrap/test_auto_peer_throttle_policy.py` и `tests/integration/test_pg_auto_peer_throttle.py` проверяют missing/untrusted peer, weak key, поддельные proxy headers, общий multiworker лимит, сохранение quota при 403, 429 без proof/audit, окно истечения и независимые loopback адреса. CI PostgreSQL upgrade/downgrade/dump/restore проверяет head `0031`. Это **не** production IP-limiter за TLS-proxy: разные клиенты behind NAT, reverse proxy, ingress sanitization, ротация HMAC, retention counters и независимый security/legal review требуют отдельного решения #130/#119. Защита от злоупотреблений не заменяет actor/role/CSRF/consent проверки.

## Двухсторонний synthetic MANAGER bootstrap (#163)

Техническая миграция `0032_auto_manager_approval` и сервис `auto_manager_bootstrap.py` реализуют **только закрытый локальный** эксперимент: заявка на MANAGER для конкретных `vehicle/location/target`, две независимые живые ADMIN-сессии, 30-минутное окно, PG17 атомарная выдача только автомобильного назначения и филиального grant, с утверждённой auto-заявкой вместо глобальной legacy MANAGER роли, плюс append-only audit. Требуются флаги `ROBOport_SYNTHETIC_AUTO_API`, `ROBOport_SYNTHETIC_AUTH_API`, `ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE` и `ROBOport_SYNTHETIC_AUTO_MANAGER_BOOTSTRAP`, каждый равен `1`; удалённая БД запрещена. Публичного API и интерфейса нет. PG17 регрессия: `tests/integration/test_pg_auto_manager_bootstrap.py`, включая запрет доступа к legacy review queue/`ticket.manage`, Alembic restore head `0032`. Изоляция от legacy-роли — обязательное условие, обнаруженное независимым Code Review.

**Порог production не изменён:** два синтетических ADMIN не подтверждают права юридического владельца сервиса. Организация, initial owner/ADMIN bootstrap, независимое утверждение MANAGER и permission lifecycle требуют нового governance/SECURITY/LEGAL gate #119. Без него нельзя применять этот механизм с реальными пользователями и ПДн.


## Автомобильный release evidence gate (#119): проверка структуры, не разрешение выпуска

Скрипт `scripts/check_auto_release_evidence.py` принимает один локальный JSON-файл с `approved_by`, `approved_at` и восемью обязательными независимыми направлениями `gates`: `auto_e2e`, `tenant_admin_acl`, `legacy_cutover_restore`, `privacy_consent`, `parts_licensing`, `payments_compliance`, `monitoring_rollback`, `ci_main`. Для каждого нужны `approved: true` и ссылка `evidence_url` вида `https://...`. Отсутствующий файл/пункт/подпись/ссылка означает BLOCKED. Проверка валидации: `PYTHONPATH=. python -m pytest -q tests/contract/test_auto_release_evidence.py`.

**Предупреждение:** скрипт проверяет **только форму** файла. Он не проверяет полномочия подписанта, существование URL, результаты аудита, полноту разрешений, действительность лицензий и не представляет собой разрешение на production. JSON может быть сфальсифицирован; нельзя строить production deploy только на exit code этого скрипта. Необходимо независимое ручное ревью, юридическое/безопасностное одобрение и отдельный защищённый approval process. При текущем состоянии исходных Issues #119/#165/#166 документ с подтверждёнными восемью гейтами **отсутствует**; production по-прежнему блокируется.

Ни CI, ни скрипт автоматически не отправляют реальные клиентские данные, платежи или запросы к PartsAPI. Ключ `PARTSAPI_TEST_KEY` нельзя помещать в evidence-файл или журналы.
