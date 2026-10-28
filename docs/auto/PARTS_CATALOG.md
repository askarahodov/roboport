# Автокаталоги 2026 — предварительный выбор

Не воспроизводить защищённые базы конкурента. OEM/aftermarket справочник, локальная номенклатура склада и коммерческие предложения — отдельные сущности. Внешняя интеграция допускается только через лицензированный API.

| Провайдер | Заявленные возможности | Что проверить до договора |
|---|---|---|
| Laximo | OEM/aftermarket, API, применяемость | договор, охват, версии, caching |
| PartsAPI | VIN, кроссы, TecDoc, нормочасы | регион, API-тариф, доступность |
| ABCP | товарные группы, кроссы, API | подписка, права на изображения |
| AutoDealer Catalog | схемы, VIN/Frame, возможный API | отдельный договор, права данных |

Материалы для проверки договорных условий: https://doc.laximo.ru/ ; https://partsapi.ru/docs ; https://www.abcp.ru/catalogs ; https://autodealer.ru/catalog . Условия лицензий, тарифы, источники данных и их актуальность на октябрь 2026 **не подтверждены**. Никаких scraping, реальных VIN/ПДн и коммерческих справочников без разрешения.

## Реально работает: offline fixture (current-dev, #231)

Модуль `apps/api/roboport_api/auto_parts_provider.py` определяет независимый Python `PartsCatalogProvider` с методами `find_vehicle`, `search_by_oem`, `list_alternatives`, `get_applicability`, `get_assemblies`. `FixturePartsCatalog` не совершает сетевых запросов, возвращает только синтетическую запись `SYN-001`, всегда `UNKNOWN` применяемость и пустые аналоги/узлы. Синтетический VIN `1HGCM82633A004352` служит исключительно тестовым примером, не подтверждением связи автомобиля/детали.

Каждая выдаваемая запись содержит `provider`, `source_revision`, timezone-aware `retrieved_at`, `brand`, `article`, `applicability` (`VERIFIED` / `UNVERIFIED` / `UNKNOWN`). Одна только cross-ссылка не повышает статус до `VERIFIED`. Поля provenance обязательны; неполная карточка отклоняется.

Невалидный VIN или номер детали даёт `INVALID_INPUT`; неизвестный корректный VIN — `NOT_FOUND`. Нет подходящих деталей/аналогов — пустой tuple, **не** подтверждение применяемости. Провайдер обязан явным образом сигнализировать `RATE_LIMITED` (429), `UNAVAILABLE` (timeout/сбой), `LICENSE_REQUIRED`; при ошибках fixture не возвращает частичные данные. Это модель доменных исключений, **не опубликованные HTTP-коды**.

Проверка без сети: `PYTHONPATH=apps/api python -m pytest -q tests/contract/test_auto_parts_provider.py`. Задача не меняет DDL, auth или публичные URL.

## Планируется (#111, #115)

Лицензированные online адаптеры, adapter sandbox, HTTP OpenAPI, проверенная применяемость и отдельный поток предложений поставщиков — **planned**, не current. Автомобильный интерфейс кладовщика для этого провайдера ещё не существует; реальные действия в UI не документируются как готовые. Перед выводом функции пользователю необходимы контрактный security-review, лицензия, scoped tenant ACL и release gate #119. Внешняя цена, наличие и ETA никогда не означают совместимость с автомобилем.


## Проверяемая граница ответов провайдера (current-dev, #233)

Модуль `apps/api/roboport_api/auto_parts_boundary.py` предоставляет чистые функции `parse_provider_part`, `parse_provider_parts`, `provider_error`. Это **не сетевой клиент, не публичный API и не доказательство наличия лицензии**. Используется исключительно при будущей интеграции после legal/security gate.

Нормализация запрещает пропуск обязательных полей `provider/source_revision/retrieved_at/brand/article/applicability`, принимает только корректную ISO-дату с timezone и один из явных статусов. Любая неверная карточка отвергает **весь** batch; предел batch — 1000 записей. Для данных cross-поиска `VERIFIED` принудительно понижается до `UNVERIFIED` — совпадение номера само по себе не является доказательством применяемости. При 429 возвращается `RATE_LIMITED`, 401/403 — `LICENSE_REQUIRED`, 404 — `NOT_FOUND`; timeout и все прочие коды дают `UNAVAILABLE`. Внешние response bodies, URL и токены никогда не включаются в исключения.

Проверка: `PYTHONPATH=apps/api python -m pytest -q tests/contract/test_auto_parts_boundary.py`. Интеграция с внешним клиентом, аутентификация запросов, аудит прав и UI **planned** в #111 и #115, не являются частью #233.
