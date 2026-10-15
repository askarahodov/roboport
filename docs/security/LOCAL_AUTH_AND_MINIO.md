# Локальная авторизация и MinIO для разработки ROBOport

**Статус:** принято как инженерное решение для первых этапов; **не реализовано** до тестов по #13, #27, #28, #25. Описания/команды — на русском, имена API — технические. До отдельного разрешения **только синтетика**.

## 1. Разделение механизмов

- **Аутентификация**: кто делает HTTP-запрос? Локальный `app_user`, подтверждённый серверной сессией, не email/телефон из запроса.
- **RBAC**: какой тип действия разрешает роль (`CLIENT`, `INTAKE`, `ENGINEER`, `STOCK`, `MANAGER`, `FINANCE`, `ADMIN`, `AUDITOR`)?
- **ACL**: имеет ли субъект явное/подтверждённое право к **этому конкретному объекту**? У клиента права на собственный `service_ticket` по `account_customer_link`, у сотрудника — по назначению либо `acl_entry`.
- **ABAC**: позволяет ли состояние выполнить действие (например, SENT estimate, SAFETY_HOLD или QC_FAILED)?

**Формула:** `allow = authenticated && RBAC(action) && object_access(subject, resource) && state_guard(resource)`. Нет совпадения — `deny`. Роль `ADMIN` не равна доступу к чужим клиентским фотографиям. Доступ к заявке бывшего владельца не наследуется при передаче робота.

## 2. Локальный вход — Issue #13

**Таблицы проекта (требуют согласования #17 и Alembic #11):** `app_user(user_id,login,status,created_at)`, `local_credential(user_id,password_hash,changed_at)`, `user_session(session_id,user_id,token_hash,created_at,last_seen_at,expires_at,revoked_at)`. Уникальные индексы на нормализованный `login`, FK, срок/статус. `customer`, `customer_account`, `account_customer_link` не смешивать. Старые SQL-черновики **не применять как есть**; `account_identity` допустим для внешнего провайдера в будущем, не заменяет локальный логин.

Пароли: Argon2id через поддерживаемую библиотеку и её настройки, индивидуальная соль, запрет plain/ reversible encryption, защита от перебора на стороне сервера. В dev аккаунты создаёт администратор/seed только из вымышленных идентичностей; публичная регистрация и email-reset отключены. Административный reset с журналом и обязательной сменой временного пароля.

Сессии: случайный opaque token генератора ОС, в базе только hash, `HttpOnly; SameSite=Lax; Secure` при TLS. HTTP без `Secure` разрешён только на `127.0.0.1` для изолированного dev — запрет выставлять наружу. Обязательны **idle и absolute TTL**, ротация при повышении привилегий, logout/revoke и отзыв на каждый запрос. Для небезопасных методов `POST/PATCH/PUT/DELETE` CSRF-token + Origin/Referer-policy, ограниченный CORS. Не хранить auth токены в `localStorage`, query string или журналах.

Черновые маршруты для включения в **OpenAPI по задаче #13**: `POST /api/v1/auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password`. Нормализованный `Error {code,message,requestId}`, ограничения `401/403/409/422/429`. Контрактный draft уже помечает защищённые операции как `localSession` cookie, но эти четыре маршрута **пока отсутствуют и НЕ реализованы**.

## 3. Роли, ACL и отзыв — Issue #27

`role(role_id,name)`, `user_role(user_id,role_id,assigned_by,revoked_at)`, `acl_entry(resource_type,resource_id,principal_id,action,created_by,expires_at,revoked_at)`. Перечень прав: `ticket:read`, `ticket:transition`, `estimate:read`, `estimate:approve`, `attachment:read`, `device:read`, `inventory:reserve`, `user:manage`, `acl:grant`. Применение через один серверный policy service; явная выдача `acl_entry` проверяет допустимость выдающего. Дополнительное право CLIENT на свой ticket основано на подтверждённой связке account↔customer и ticket applicant.

**Тесты:** клиент A знает UUID заказа B → отказ; робот передан B → история A скрыта; ADMIN без полномочия к делу не читает приватный attachment; role/ACL revoke → следующий запрос отказ; idempotent replay не обходит повторную авторизацию; `X-Customer-ID` игнорируется. Положительные проверки выполняются на настоящей тестовой PostgreSQL, а не в fake token fixtures.

## 4. MinIO для файлов — Issues #28 и #25

**MinIO выбран как dev S3 backend**, не публичное облако. Метаданные `attachment` остаются в PostgreSQL, бинарные синтетические PDF/фото — в buckets `roboport-documents-dev`, `roboport-quarantine-dev`. Код домена использует интерфейс `FileStorage`, не привязан к MinIO SDK.

Dev стенд: Docker Compose c **закреплённым image version/digest**, API и console bind только к `127.0.0.1`, named-volume, пароли в gitignored `.env.local`, **отдельные минимальные app credentials**, анонимный READ/LIST закрыт. MinIO не знает наших ACL: каждый запрос файла проходит RBAC+ACL/ownership и состояние quarantine через API. В первом срезе отдавать **stream через backend**; presigned URLs — только отдельный ADR из-за риска доступа после отзыва. Объектные ключи случайные, без имени/email/серийного номера.

**Проверить в реальном dev MinIO:** загрузку и получение синтетического объекта, невозможность anonymous get/list, закрытие чужого и quarantined attachment, ошибки/повторы, отсутствие orphan metadata, cleanup test volume. Нельзя считать MinIO запущенным без `docker compose` и отрицательных integration tests.

## 5. Порядок исполнения

1. #10 CI и lockfiles → #15 OpenAPI/contract.
2. #17 модель данных → #11 одноразовая PostgreSQL и Alembic.
3. #13 локальный вход/сессии → #27 RBAC+ACL.
4. #28 приватный MinIO **параллельно** с #13/#27 (после #10), затем #25 FileStorage/ACL и UI документы #23.
5. Keycloak/OIDC — только будущий ADR, не блокер первых спринтов. Публичный запуск отдельно блокируют юридические и security gates.

Документы: `TECHNICAL_SPEC.md`, `docs/architecture/ADR.md`, `docs/security/SECURITY_MODEL.md`. Нельзя передавать IAM/root MinIO credentials в браузер или считать скрытие кнопки в React авторизацией.
