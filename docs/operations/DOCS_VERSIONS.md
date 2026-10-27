# Версии базы знаний и журнал проверенных dev-срезов

> **Статус: current, только внутренний синтетический dev.** Это не пользовательские release notes автосервиса и не свидетельство допуска в production. Версия документации равна неизменяемому Git SHA версии кода, а не текущему состоянию `main` в момент открытия ссылки.

## Почему это важно

База знаний на Docsify 5.0.0 читает Markdown напрямую из `docs/`. Если документация меняется после тестов, открытая ссылка на `main` уже не соответствует проверенному приложению. Поэтому каждый checkpoint привязывается к **commit SHA + GitHub Actions run + статусу dev/legacy/planned**. Выпуск без этих данных не считать проверенным.

## Журнал инженерных checkpoint

| Дата | Git SHA / CI | Что подтверждено | Статус |
| --- | --- | --- | --- |
| 26.10.2026 | [`c8fe1695`](https://github.com/askarahodov/roboport/commit/c8fe1695ff37e645f5bbc051886d098b61257cc9), [CI #40327490281](https://github.com/askarahodov/roboport/actions/runs/40327490281) | PostgreSQL, API, Web и Docsify/Playwright; внедрение локального docs-портала, не публикация | dev checkpoint |
| 27.10.2026 | [`9d5b57a1`](https://github.com/askarahodov/roboport/commit/9d5b57a119c57b661c0ade558ba88a81587a7438), [CI #40492677681](https://github.com/askarahodov/roboport/actions/runs/40492677681) | Четыре обязательные CI jobs успешны после изменений синтетического auto proof | dev checkpoint |

Записи отражают только реально проверенные на дату SHA. Для следующих срезов добавлять строку после зелёного post-merge CI; не переписывать историю и не объявлять весь функциональный backlog завершённым.

## Как сверить версию документации

В корне checkout:

```bash
git rev-parse HEAD
python scripts/check_docs_links.py
cd apps/web
npm ci
npx playwright install --with-deps chromium
npm run test:docs:e2e
```

Для **Windows CMD** с установленными Git, Python 3, Node.js и браузером:

```cmd
git rev-parse HEAD
python scripts/check_docs_links.py
cd apps\web
npm ci
npx playwright install chromium
npm run test:docs:e2e
```

Скрипт `check_docs_links.py` проверяет локальные Markdown ссылки и якоря; браузерный прогон проверяет реальные Docsify переходы/поиск. Browser smoke на Windows требует своего локального прогона — зелёный Linux CI не заменяет его.

## Принцип версионирования и будущей публикации

1. Зафиксировать **конкретный commit SHA** вместе с приложением, OpenAPI, миграциями, документацией и тестами. Не ссылаться только на `main` для юридически или эксплуатационно важных инструкций.
2. В GitHub Actions проверить для этого SHA Markdown links/anchors, Docsify navigation, TypeScript/Web, API/contract, PostgreSQL миграции и browser E2E. В журнал записать URL run и результат. Красный или отменённый CI не является доказательством.
3. Перед потенциальным выпуском сверить названия кнопок, права, версии API и данные на тестовом окружении. Текущие экраны помечать `current (dev)`, отсутствующие — `planned`, робототехнические — `legacy`. Скриншоты только синтетические.
4. Для будущего публичного релиза после #119: утвердить privacy/security/legal/brand review, способ хранения документации по release tag, маршрутизацию между версиями, lifecycle и rollback. **Теги и публичный deployment пока не создаются.**
5. Текущий `docs/index.html` загружает pinned Docsify и search plugin с CDN. Локальный просмотр **не означает автономный/offline или self-hosted пакет**: требуется отдельно решить закрепление integrity, доступность CDN и поставку собственных статических ассетов перед публикацией.

Можно получить исходники документации ровно для выбранного commit без скачивания новой версии `main`:

```bash
git archive --format=zip --output=roboport-docs-snapshot.zip <commit-sha> docs
```

Заменить `<commit-sha>` на реальный SHA из журнала. Этот ZIP содержит **исходники Markdown/Docsify конфигурацию**, но не CDN JavaScript/CSS и не является готовым автономным сайтом.

## Границы готовности

- **Реализовано:** внутренний Docsify, sidebar/search, проверка Markdown ссылок, Playwright проверка навигации и обязательный docs impact в PR.
- **Не завершено:** публичная доставка, версии как отдельные сайты, полноценный audit accessibility, self-hosted Docsify assets, юридическое одобрение и эксплуатационный выпуск.
- **Изменения интерфейса:** обновлять руководства затронутых ролей в том же PR; см. [политику документации](../DOCUMENTATION_POLICY.md).

Актуальные задачи: [#125 — публикация и навигация](https://github.com/askarahodov/roboport/issues/125), [#119 — quality/release gate](https://github.com/askarahodov/roboport/issues/119).

## Проверка визуальной доступности dev-портала (#125)

Проверяемая dev-версия Docsify поддерживает цветовую схему ОС Light/Dark без сохранения user preferences и без внешних cookie. Playwright выполняет фактический расчёт контраста на пяти типах элементов, проверяет root overflow в окнах 390 и 1280 px, последовательность Tab/Enter и видимый keyboard-focus. Эти проверки **не** являются сертификатом WCAG всей будущей системы, не заменяют screen-reader/assistive technology аудит и не разрешают публичный release. CI сохраняет обязательную проверку внутренних ссылок Markdown/anchor и реальных sidebar/search переходов.

**Решение по публикации:** единственный утверждённый вариант сегодня — локальный docs-only dev HTTP-сервер, никогда не открытый в публичную сеть. Внешние pinned CDN Docsify assets — **принятый только для dev риск**, а не гарантия доступности и цепочки поставки; автономная self-host доставка не реализована. Версионная архивация по SHA и будущая публичная доставка остаются блокированы #119/#121 до отдельно проверенного решения. Journal rows добавлять только после post-merge CI (URL + точный commit); дата в таблице не заменяет hash.
