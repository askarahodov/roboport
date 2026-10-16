# Независимая проверка OpenAPI — задача #15

**Версионный контракт:** `contracts/openapi.yaml`, версия `0.16.0-draft`, OpenAPI 3.1.2. Зафиксированные 46 идентификаторов операций находятся в `contracts/operation_ids.json`.

## Команды проверки

```bash
python -m pip install -r apps/api/requirements-dev.txt -c apps/api/constraints-py313.txt
python -m pip install openapi-spec-validator==0.7.2
python scripts/check_contract.py
python -m openapi_spec_validator contracts/openapi.yaml
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap tests/contract
cd apps/web
npm ci
npm run generate:api
git diff --exit-code -- src/shared/api/schema.d.ts
npm run typecheck
npm run build
```

Скрипт выполняет **настоящую независимую проверку OpenAPI 3.1** через `openapi_spec_validator.validate_spec` и дополнительные проектные ограничения: список 46 операций с методами GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS/TRACE, успешные 2xx, единая схема ошибок для задекларированных HTTP 400/401/403/404/409/422/429, неизменяемый состав полей `Error {code,message,requestId}`, схема локальной непрозрачной cookie-сессии `rp_session`.

Мутационные тесты специально повреждают `operationId`, `$ref`, 2xx, Error, 429, методы HEAD и имя cookie, чтобы проверить, что CI действительно отказывает в неверном контракте.

## Граница реализации

HTTP schema-тестами независимо проверены только три общедоступных каталожных GET: `/api/v1/catalog/brands`, `/api/v1/catalog/models`, `/api/v1/catalog/models/{modelId}`. **Остальные 43 операции остаются проектными декларациями**, а не подтверждёнными production handlers. Синтетические обработчики из отдельных задач не меняют эту границу.

Генератор типов `openapi-typescript@7.10.1` и его зависимости фиксируются `apps/web/package-lock.json`. В CI типы повторно создаются и сравниваются с репозиторием: `git diff --exit-code`. Python-валидатор фиксирован версией 0.7.2 в CI. Реальные данные, платежи, внешняя публикация и OIDC не входят в задачу.
