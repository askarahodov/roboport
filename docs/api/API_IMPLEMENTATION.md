# Правила реализации REST API

**Контракт:** `contracts/openapi.yaml` (OpenAPI 3.1.2, **проектные** 42 операции / 56 схем). Число операций в файле не означает число готовых серверных маршрутов. Реализованы только read-only catalog endpoints и служебные health/meta. Остальные — backlog.

## Общие требования

- Префикс `/api/v1`; `operationId` стабилен, изменения проходят review. Использовать UUID, UTC, minor units для RUB; cursor-пагинация должна быть стабильной.
- Тела ошибок: `Error {code,message,requestId}`; сервер создаёт `X-Request-ID`, не доверяет клиентскому значению. Клиентский ввод, PII и токены не включать в error text/logs.
- Проверить happy response и отказные 400/401/403/404/409/422; далее 429/500. Ошибка на чужой объект не раскрывает существование чужой заявки или документа.
- Защищённые маршруты используют `localSession` cookie из OpenAPI, не JWT Bearer. `POST /api/v1/auth/login`, logout, me, change-password и CSRF-контракт добавить в #13/#15; не выдавать проектную схему за действующий login.
- Каждый mutation endpoint: серверная сессия + роль RBAC + право ACL на объект + актуальное состояние + `Idempotency-Key`; replay возвращает прежний результат при одинаковом fingerprint, другой payload даёт 409, отзыв прав проверяется до replay.
- Не выдавать приватный документ прямым URL по ID: клиент запрашивает доступ, сервер проверяет ACL и отдаёт контролируемый поток из приватного dev MinIO; signed URL только после ADR.

## Порядок добавления маршрута

1. Сверить requirement, policy, state transition и конкретный `operationId`.
2. Обновить контракт (или зафиксировать расхождение ADR), добавить Request/Response/Errors.
3. Реализовать use-case с guard и транзакцией, отдельно от FastAPI роутера.
4. Добавить HTTP contract tests по JSON Schema и негативный BOLA кейс.
5. Зафиксировать реальный вывод lint/test в PR. Самописный `scripts/check_contract.py` — только структурный preflight, не полноценная независимая OAS-проверка.

## Реализованная первая часть

`GET /api/v1/catalog/brands`, `GET /api/v1/catalog/models`, `GET /api/v1/catalog/models/{modelId}` — только синтетические данные. `/health/ready` возвращает 503 до подключения обязательных dev-сервисов намеренно. `GET /api/v1/customer/me` не реализован и без авторизации не выдаёт тестовый профиль.

Интеграция с frontend осуществляется через типизированный OpenAPI client после #15; нельзя вручную придумывать новые payload без изменения контракта.
