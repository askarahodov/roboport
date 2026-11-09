# Решение по закрытым legacy PR — инвентаризация #165

> **Статус:** аудит источников и решение о переносе, **не** выполнение cutover/decommission.
> Основание: закрытые **без merge** PR #1, #45, #46, #47, #59, #60; сверка их changed files / patches с актуальными путями `main` 09.11.2026. Этот документ не разрешает merge исходных веток.

## Матрица решений

| PR (head SHA) | Уникальное содержимое и риск | Сверка с `main` | Решение |
|---|---|---|---|
| [#1](https://github.com/askarahodov/roboport/pull/1) (`59686f8`) | Первичный монорепозиторий, ТЗ, robot DDL/OpenAPI, CI, брендинг. | `AGENTS.md`, `README.md`, `contracts/openapi.yaml`, `docs/`, `apps/` существуют, но текущая автомобильная цель заменила исходную. | **ARCHIVE**: не восстанавливать старую спецификацию/маршруты. Исторические материалы хранить как legacy. |
| [#45](https://github.com/askarahodov/roboport/pull/45) (`303a090`) | Сессионно привязанный signed keyset cursor, отказ от неканонического base64, атомарный ACL ticket-read, отрицательные тесты. | В `apps/api/roboport_api/keyset_cursor.py` уже присутствуют canonical-base64/HMAC/session/state guards; `authorization.py` и тесты legacy существуют. | **KEEP EXISTING**, **REIMPLEMENT FOR AUTO ONLY IF NEEDED**: не переносить `service_ticket` и его роли. Для автомобильного списка отдельно проверить staff/customer object scope, revoked grant, pagination, фильтры и key rotation. |
| [#46](https://github.com/askarahodov/roboport/pull/46) (`18cee02`) | Приватный loopback MinIO, dev compose, policy, отдельный workflow; риск стороннего OCI-образа. | `infra/dev/compose.minio.yml`, `infra/dev/minio-app-policy.json`, `.github/workflows/minio-dev.yml` уже существуют. Образ `tobi312/minio` закреплён digest. | **KEEP DEV INFRA ONLY**: не менять origin/provenance образа без проверки; production только после #119, подписи и supply-chain review. |
| [#47](https://github.com/askarahodov/roboport/pull/47) (`4abfa1c`) | `FileStorage` для приватного dev MinIO: ограничение MIME/размера, quarantine, тесты. Базируется на ветке #46. | `apps/api/roboport_api/storage.py` уже существует и прямо требует domain ACL перед вызовом; `tests/integration/test_minio_storage.py` существует. | **KEEP ADAPTER, REVALIDATE AUTO BOUNDARY**: не публиковать ключи/бакеты, не считать storage ACL. Для актов СТО нужен серверный tenant+object authorization и соответствующие интеграционные тесты. |
| [#59](https://github.com/askarahodov/roboport/pull/59) (`b98b4cc`) | `device_ownership` c robot FK, одно активное владение, история владельцев и PG17 тесты. | Старой миграции `0015_device_ownership.py` в `main` нет; автомобильные миграции `0021_auto_vehicle.py`, `0022_auto_vehicle_claim.py` и отдельный ownership workflow есть. | **DO NOT PORT TABLE**: переносить только проверяемый принцип «история прежнего владельца не наследуется» через автомобильные grants, proof и audit; до #119 реальных владельцев не мигрировать. |
| [#60](https://github.com/askarahodov/roboport/pull/60) (`98ae7a5`) | Robot SKU/variant/dock, две независимые роли для допуска совместимости, evidence. | В `main` есть другой `0015_catalog.py`, но `catalog.py` пустой; это **не** доказательство реализации PR #60. | **DO NOT CHERRY-PICK**: robot model/dock/category несовместимы с automotive; пересмотреть отдельно для #111/#447 только шаблон evidence/provenance и независимого согласования. Нужен автомобильный OEM/P-N/region/vehicle контракт и права. |

## Проверенные границы

- Повторное использование технической идеи **не** означает перенос таблиц, ролей, данных, HTTP endpoints или acceptance evidence.
- Ветка #47 опиралась на #46: независимо от того, сколько файлов сохранилось в `main`, merge обеих закрытых веток запрещён без нового diff и security review.
- Имена `0015_catalog` в #60 и в `main` совпадают, но SQL-модели различаются. **Особый риск:** попытка наката миграции #60 поверх современного дерева недопустима.
- Наличие `storage.py`, ACL и MinIO в `main` не доказывает пригодность к реальным ПДн. Ограничение #119 остаётся в силе.
- Из закрытых PR **не найдено оснований** для переноса robot customer history в `auto_vehicle` по VIN, robot device ID или совпадению владельца.

## Привязка к автомобильному backlog

- **#165 / #119 (P0):** запрет бесконтрольного merge старых веток, инвентаризация robot URLs/API/DDL, production approvals, backup+restore и отдельный decommission.
- **#130 / #112:** проверить автомобильные grants/права истории при смене владельца; не копировать `device_ownership`.
- **#111 / #447:** оценить необходимость provenance и review для automotive compatibility; предметные сущности проектировать с нуля.
- **#439 / #441:** применить минимальный object-level ACL и quarantine к документам СТО при появлении настоящего загрузочного API; текущий FileStorage сам права не проверяет.

## Acceptance / verification

Проведены read-only осмотр metadata, changed filenames и patches шести закрытых PR и проверка наличия перечисленных текущих файлов в default branch. **Не запускались** локальные PG17/HTTP/MinIO/E2E тесты; содержимое закрытых веток не собрано, полное семантическое равенство всех файлов старых PR не утверждается. Для этого документа достаточно проверить Markdown-ссылки и review решений; реальная функциональность требует отдельных тестируемых автомобильных PR.

**Rollback:** документ можно удалить одним revert без изменения схемы/инфраструктуры/пользовательских данных. Никаких реальных секретов, личных данных, SQL DDL и релизных операций не затрагивалось.

## Дальнейший gate

Финальное закрытие #165 возможно **только** после полного route/DDL/attachment inventory, подтверждённой миграции и сверки архивов, восстановления, отрицательных BOLA тестов, юридического/privacy/security одобрения #119, отдельного cutover PR и post-merge CI. Этот PR закрывает лишь bounded работу по disposition закрытых legacy PR, **не** #165.
