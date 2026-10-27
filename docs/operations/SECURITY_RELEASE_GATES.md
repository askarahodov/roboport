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
