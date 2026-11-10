# Номенклатура: SKU, аналоги, версии цен, XLSX и безопасные дубли — БоксРитм

**Статус: current-dev, только synthetic HTTPS loopback с PostgreSQL 17**. Задача [#447](https://github.com/askarahodov/roboport/issues/447), product [#168](https://github.com/askarahodov/roboport/issues/168). Нет поставщика, OEM fitment, лицензионного каталога, реальной бухгалтерской цены, налоговых документов и разрешения #119. Это расширение склада, а не отдельная/legacy robot номенклатура.

## Целостный бизнес-сценарий

1. WAREHOUSE+MANAGER одного филиала входит в `/staff/warehouse-catalog` через `/staff` и указывает UUID тестовых организации и филиала. Для **записи** нужны оба живых гранта и документные `SUPPLIER_ORDER.CREATE/EDIT/CLOSE, PRICE_PURCHASE`, `ESTIMATE.EDIT, PRICE_SALES`. Read-only сотрудники видят метаданные, цены выдаются только при явных эффективных разрешениях; прямой URL и старый токен не дают дополнительных прав.
2. В «**Карточка новой детали**» создаёт артикул (канонический A–Z/цифры/точка/слэш/дефис), неизменяемую категорию, `PCS|L|KG`, опциональные штрихкод и бренд, **тестовые** закупочную и продажную цены (целые копейки RUB). В одной транзакции появляются `auto_stock_sku`, `auto_catalog_profile`, `auto_catalog_price_batch` и первая версия `auto_catalog_price_event`; физический остаток не меняется. Повтор с тем же `Idempotency-Key` возвращает прежний результат, с иным телом — 409.
3. «**Пакетные версии цен**»: одна строка `SKU UUID;expectedVersion;purchaseMinor;saleMinor`, до 100 SKU за операцию. Сервер сверяет все текущие версии под блокировками SKU и филиала; либо проводит **всю пачку** в append-only событиях, либо не проводит ни одной. Цена в старой приходной накладной/сметной версии не переписывается. Для иных стоимостных документов ценовые полномочия проверяются их **собственными** API — этот экран не является обходным путём на изменение проведённой оценки.
4. «**Аналоги**»: запись пары разных SKU внутри одного филиала, категории и единицы измерения с причиной. Это только **непроверенная ручная подсказка**; связь не заменяет механизма применяемости OEM/подбора по VIN и не пересчитывает stock.
5. «**Объединить SKU**»: выберите source и target, подтвердите необратимое **логическое** объединение. Источник должен иметь 0 единиц `on_hand` и `reserved`, без распределённых остатков по lot и без серийной истории; метаданные category/uom и непустые brand/barcode не могут противоречить target. PostgreSQL проверяет это при INSERT, предотвращает цепочки/cycles и повторные источники. `auto_catalog_merge` лишь помечает source→target: **ни одна историческая FK** в складских документах, партиях, ценах или заказ-нарядах не меняется; старые документы по UUID источника доступны, а новый прайс источнику уже не присвоить. Не является переносом реального физического склада.
6. «**XLSX: проверка → подтверждение**»: примите маленький синтетический файл: одна видимая worksheet, заголовки **строго** `article,category,uom,barcode,brand,purchaseMinor,saleMinor,analogArticle` (8 колонок); 1–100 строк, файл до 128 000 байт. `POST .../imports/preview` с неизменяемыми байтами и UUID ключом проверяет формулы, макросы, внешние ссылки, скрытые листы, дубль артикула/штрихкода, цены и совместимость; записывает лишь staged snapshot с SHA-256 и 15-минутным TTL. В результате у каждой строки видна существующая версия или создание нового SKU. **Нажмите «Подтвердить весь XLSX атомарно»**: `POST .../imports/commit` проверяет live ACL, точный digest и optimistic version каждого SKU, только потом создаёт карточки, ценовые версии, связи аналогов и immutable commit receipt в одной транзакции. Изменённая между preview и commit цена даёт 409 и **0 изменений этого импорта**. Неизвестный ответ POST повторять тем же ключом и телом.
7. «**Экспортировать XLSX**» возвращает `.xlsx` с текущими profiled/active SKU, ценами и одной первой связью аналога. Для экспорта требуются **обе** цены и отсутствие historical cutoff: отказывать, а не выгружать неполные/скрытые стоимостные данные. Excel-файлы создаются с `inlineStr`, без формул. Для нескольких аналогов сохраняйте полный журнал связей в PostgreSQL: текущий XLSX-формат включает **первый** аналог по алфавиту и не является roundtrip всех связей.

## Контракт (закрытый dev-only API; `Cache-Control: no-store`)

База `/api/v1/dev/auto/organizations/{orgId}/warehouse/catalog`:

| HTTP | Операция |
|---|---|
| GET `?locationId=&limit=&afterSkuId=` | Живой scoped список, cursor, скрытые цены как null и флаги hidden |
| POST `/skus` | Новый SKU+профиль+исходная ценовая версия |
| POST `/prices` | 1–100 entries с обязательным expectedVersion, атомарный append-only batch |
| POST `/analogs` | Несимметричный payload left/right нормализуется в неизменяемую упорядоченную пару |
| POST `/merges` | Alias source→target без перепривязки FK, строго без stock/serial source |
| POST `/imports/preview?locationId=` | Сырые XLSX bytes, MIME `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`; staged sha256+snapshots |
| POST `/imports/commit` | `{locationId,previewId,sha256}`, atomic import/price/analogs и receipt |
| GET `/export?locationId=` | Полный доступный XLSX или 403/409 |

Каждая POST операция требует действующей `rp_session`, `Origin`, `X-CSRF-Token`, UUID `Idempotency-Key`; session/grants/ACL пересматриваются **до replay**. Чужой tenant/branch, отозванная роль, DENY/исторический cutoff → 403; stale preview, несовместимый alias, повтор с изменённым payload, price race → 409; некорректный JSON/XLSX, числа/поля/UUID, ZIP-бомба, формулы и недоверенные внешние ссылки → 422. POST result `syntheticOnly:true`. MIME/zip проверить до интерпретации любого импортного значения; при 503 старый POST мог успешно примениться.

## Безопасность, таблицы, доказательства и откат

- Миграция `apps/api/alembic/versions/0124_auto_catalog.py`: неизменяемые `auto_catalog_profile`, `auto_catalog_price_batch`, `auto_catalog_price_event`, `auto_catalog_analog`, `auto_catalog_merge`, `auto_catalog_import_preview`, `auto_catalog_import_commit`; SQL guards и REVOKE PUBLIC. **Старые `auto_stock_sku`, stock ledger и исторические FK не меняются.** Новые SERIAL/OEM claims не создаются.
- Приложение `apps/api/roboport_api/auto_sku_catalog.py`, HTTP `auto_sku_catalog_http.py`, XLSX `auto_catalog_xlsx.py`. Использует ограниченный OOXML parser `scripts/auto_synthetic_xlsx.py`, уже принятый проектом для synthetic import.
- RU UI `apps/web/src/pages/staff/WarehouseCatalog.tsx`, route и роль-гайд. PG17/HTTPS `tests/integration/test_pg_auto_sku_catalog_http.py`: create/replay, XLSX preview/commit, 409 stale, atomарность, FK history, ACL, конкуренция. Browser `apps/web/e2e/warehouse-catalog.spec.ts`: 390/dark и 1280, поток, 403, replay.
- До слияния нужны 5/5 PR CI (api, postgres, web, browser-e2e, windows-cmd), независимый code review, после слияния 5/5 на конкретном merge SHA. Независимая human QA #76/#92 и security/legal #119 **не PASS** без своих доказательств.
- **Откат:** только revert PR и `alembic downgrade 0123_supplier_booking` на одноразовой PG17 базе, без допустимого удаления реальных данных. Новые append-only каталожные записи сохраняют происхождение и не могут быть стёрты без нарушения аудита. В production ни ролевой/юридический gate, ни контрагенты, ни расчёт фактической маржи этим PR не открываются.

**Известные ограничения:** для объединения не переносится stock и не переписываются связанные записи; исторические SKU продолжают существовать физически. Обновление присвоенной при создании категории, UOM и штрихкода через эту версию API запрещено. Отдельное требование #111 — лицензированный провайдер и проверенная применяемость — остаётся `planned`.
