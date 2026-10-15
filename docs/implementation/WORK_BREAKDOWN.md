# Реестр задач разработки (GitHub — источник истины)

**Важное исправление:** ранний список `RP-001…RP-020` конфликтовал с уже созданными GitHub Issues #10–#14 (RP-001 обозначал и пакет репозитория, и CI). Он снят с роли нумератора. **Номера `#N` из GitHub уникальны; зависимости и PR всегда ссылаются на них.**

| GitHub Issue | Содержание | Блокирующие зависимости | Проверка завершения |
|---|---|---|---|
| [#10](https://github.com/askarahodov/roboport/issues/10) | CI и lockfiles | — | 2+ зелёных workflow jobs и воспроизводимая сборка |
| [#14](https://github.com/askarahodov/roboport/issues/14) | Дизайн-handoff для dev | — | исходники C, две темы, tokens, UX матрица |
| [#15](https://github.com/askarahodov/roboport/issues/15) | Независимая OpenAPI-валидация | #10 | OAS lint, schema 2xx/4xx, SDK smoke |
| [#16](https://github.com/askarahodov/roboport/issues/16) | React frontend foundation | #10, #14 | build, routing, dark/light/auto, Playwright |
| [#17](https://github.com/askarahodov/roboport/issues/17) | Каноническая модель данных/FSM | #15 | ADR расхождений и тестируемые схемы |
| [#11](https://github.com/askarahodov/roboport/issues/11) | Alembic PostgreSQL baseline | #10, #17 | upgrade/rollback на disposable PG |
| [#12](https://github.com/askarahodov/roboport/issues/12) | Публичный каталог end-to-end | #10, #15, #16 | API+UI read-only, отрицательные HTTP tests |
| [#18](https://github.com/askarahodov/roboport/issues/18) | Совместимость техники и допуски | #11, #17 | variant/SKU/region/revision guards |
| [#13](https://github.com/askarahodov/roboport/issues/13) | Реальный OIDC/ABAC | #11, #17 | JWT+2 клиента, отзыв, BOLA deny |
| [#19](https://github.com/askarahodov/roboport/issues/19) | Заявка/FSM/safety/audit | #11, #13 | транзакции и отказ опасных переходов |
| [#20](https://github.com/askarahodov/roboport/issues/20) | Сметы и согласие | #19, #13 | immutable SENT + версия + replay |
| [#21](https://github.com/askarahodov/roboport/issues/21) | Склад и резервы | #18, #20 | no negative stock, 2-worker race |
| [#22](https://github.com/askarahodov/roboport/issues/22) | Outbox | #19, #20 | atomic write + retry/duplicate tests |
| [#23](https://github.com/askarahodov/roboport/issues/23) | Клиентский кабинет | #13, #16, #19, #20 | Light/Dark + 2 клиента + 6 экранов |
| [#24](https://github.com/askarahodov/roboport/issues/24) | CRM | #16, #19, #21 | 7 рабочих сценариев + QC FAIL |
| [#25](https://github.com/askarahodov/roboport/issues/25) | Документы и хранение файлов | #11, #13, #23 | private download + audit, storage adapter |
| [#26](https://github.com/askarahodov/roboport/issues/26) | Интеграционные / release проверки | #21…#25 | реальный dev контур + доказательства security/QA |

Список задач будет добавляться по мере декомпозиции. **Созданы ли эти Issues и их текущий статус нужно сверять в GitHub**, а не считать выполненными по наличию строки здесь. Таблица описывает целевую структуру, не текущие достижения.
