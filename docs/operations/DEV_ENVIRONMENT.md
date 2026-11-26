# Окружение разработки ROBOport / Airmatik

**Статус:** current для синтетического robot-domain dev стенда; авто-функции пока строятся по этапам. Раздел не даёт разрешения на production. Дата сверки: 26.10.2026.

## Требования

Docker Engine + Compose v2, Python 3.13, Node.js 24, npm и Git. Для полноценного dev требуется одноразовый PostgreSQL 17 и приватный MinIO, при OIDC — Keycloak 26.3.3. Синтетический локальный стенд описан в [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md).

## Быстрые проверки (Linux shell; агент использует GitHub CI)

```bash
python -m pip install -r apps/api/requirements-dev.txt -c apps/api/constraints-py313.txt
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap tests/contract
python scripts/check_contract.py
cd apps/web
npm ci
npm run typecheck
npm run build
```

Эти команды проверяют код/контракты, но PG интеграционные тесты требуют отдельного подготовленного стенда.

## Запуск инфраструктуры dev

Создать приватный `infra/dev/.env.local` на основе `infra/dev/local.env.example`, заменить все `CHANGE_TO_...` уникальными локальными значениями. Затем:

```bash
bash infra/dev/bootstrap-local.sh
docker compose --env-file infra/dev/.env.local -f infra/dev/compose.local.yml ps
```

Bootstrap запускает Compose инфраструктуру PostgreSQL/MinIO/Keycloak, применяет Alembic и подготавливает синтетические аккаунты. Web/API **не** контейнеризованы вместе с этим dev-стендом: они запускаются на host и используют локальные ограничения безопасности. Для корректного единого HTTPS origin использовать [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md), команду `bash infra/dev/start-https.sh` и соответствующие сертификаты только в отдельном тестовом профиле браузера.

## Запуск API без БД (ограниченный public smoke)

```bash
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
```

`/health/live` возвращает 200, `/health/ready` без служб может намеренно возвращать 503. Не обходить проверку HTTPS/loopback и не публиковать этот dev стенд.

## Целевая платформа сервера (Linux-only)

[Решение заказчика 26.11.2026](https://github.com/askarahodov/roboport/issues/119#issuecomment-7187571899): сервер, Docker Compose, PostgreSQL, API и эксплуатация только Linux. Нативный CMD/PowerShell/Windows bootstrap исключён из обязательного scope #119; при разработке с Windows использовать удалённый Linux dev или Linux VM/WSL2 как Linux-окружение, не как поддержку Windows server. Пользовательский браузер на Windows не исключён. Linux Playwright не доказывает специфичное для Windows браузерное поведение.

## Тесты и безопасные операции

Дополнительный Linux Compose smoke проверяет реальные PostgreSQL/Keycloak, прямой HTTPS и synthetic login → identity/vehicles → CSRF logout → удаление cookie и отдельный отказ при replay исходного session cookie; retired catalog обязан возвращать 404. См. [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md). Не заменяет полный CI.

`apps/web/package-lock.json` присутствует, `npm ci` проверяется CI. Внешние OIDC, локальные сессии, PostgreSQL и MinIO **уже тестируются** на синтетике, но не означают production-readiness. Скрипты миграций нельзя применять к действующей базе без backup/rollback. `docker compose ... down -v` **удаляет данные dev volumes**. Не использовать реальные VIN, телефоны, клиентские обращения или секреты в Git и screenshots.

Подробности: [модель безопасности](../security/LOCAL_AUTH_AND_MINIO.md), [документационный аудит](../auto/DOCUMENTATION_AUDIT.md).

## GitHub Actions — политика параллельных прогонов (#135)

Тяжёлый workflow `.github/workflows/ci.yml` запускается на каждый `pull_request`, при push в `main` и по ручному `workflow_dispatch`. Отдельный `push` feature-ветки **не** создаёт второй идентичный прогон, если PR уже открыт. В рамках одного PR новые изменения отменяют устаревшие CI-прогоны через `concurrency.cancel-in-progress`, но не отменяют проверки других PR или `main`.

Не закрывать PR, пока **последний head SHA** не прошёл PostgreSQL, API, Web и Browser E2E. Если браузер не запускается, это инфраструктурный блокер, а не `PASS`. Для обычного host-runner запускать `npx playwright install --with-deps chromium`; для официального Playwright container проверять реальный `chromium.launch()` и все browser E2E. При проблемах проверять browser smoke, GitHub Actions run и публикацию `trace` для ошибок тестов.

## История browser-кэша до перехода на Playwright image (#135)

Историческая конфигурация **до PR #150** (27.10.2026): browser job восстанавливал npm-кэш через `actions/setup-node@v4` и Chromium-кэш через `actions/cache@v4` по `apps/web/package-lock.json`. Начиная с PR #150, Chromium-кэш удалён: браузер поставляется официальным версионированным Docker-образом, npm-кэш остался. `@playwright/test` закреплён в `apps/web/package.json` версией `1.56.1`. Изменение lockfile автоматически меняет ключ кэша: не использовать несовместимые версии Chromium через общий `restore-keys`. В прежней конфигурации cache miss приводил к загрузке браузера и установке системных библиотек; на host-runner команда `npx playwright install --with-deps chromium` обязательна. В текущем контейнерном browser job системные библиотеки заранее установлены, а реальный запуск Chromium проверяется fail-fast. Обязательные четыре job и публикация traces при падении сохранены.

Из логов browser job **до кэша**, считая от начала шага установки до начала следующего шага (приблизительная длительность):

| Run | SHA/контекст | Chromium install (приблизительно) |
| --- | --- | ---: |
| [40327490281](https://github.com/askarahodov/roboport/actions/runs/40327490281) | main, 26.10.2026 | 22,6 с |
| [40492285963](https://github.com/askarahodov/roboport/actions/runs/40492285963) | Auto proof #130, 27.10.2026 | 19,9 с |

Проверка на одном SHA `8e62e4da77044f12a4290c93986303431f6e0b20`, в [run 40493776383](https://github.com/askarahodov/roboport/actions/runs/40493776383) (re-run browser, attempt 2). Замеры получены из временных отметок GitHub job logs; полный цикл от первой до последней строки, не wall time workflow:

| Режим | Chromium install | Browser job целиком | Кэш Chromium |
| --- | ---: | ---: | --- |
| Cold (первый прогон, job `122153944688`) | ~22,8 с | ~87,0 с | cache miss, скачивание; сохранение ~267 MB |
| Warm (повтор, job `122154916868`) | ~15,5 с | ~74,1 с | cache hit, браузеры не скачиваются |

**Результат исторического сравнения до PR #150:** повторный запуск быстрее примерно на 7,3 с для install и на 12,8 с в целом. Это не статистическая гарантия: системные `--with-deps` выполняются всегда, время сетевых операций/планирования runner может отличаться. Оба прогона имели зелёные четыре CI jobs; браузерные E2E и Docsify прошли. По результатам root-cause анализа эта схема **заменена** официальным образом в PR #150. Остался отдельный вопрос безопасного path-based impact для browser job. Нельзя отключать `Browser E2E` или Docsify smoke ради скорости; `--with-deps` обязателен при использовании отдельного host-runner без предустановленных ОС-зависимостей. Учитывать номер run и проверяемый head SHA.

Локальный smoke Docsify (не требует запущенных PostgreSQL/API; нужен Python 3 в `PATH`):

```bash
cd apps/web
npm ci
npx playwright install --with-deps chromium
npm run test:docs:e2e
```

Для полноценного `npm run test:e2e` необходим запущенный synthetic FastAPI на `127.0.0.1:8000` с тестовыми зависимостями; без production данных. Агент выполняет эти проверки в Linux GitHub Actions.

## Текущая browser CI-конфигурация на официальном Playwright image (#135)

**Root cause cold-run:** [main CI 40494924138](https://github.com/askarahodov/roboport/actions/runs/40494924138) потратил ~469,6 с, а [PR CI 40495144700](https://github.com/askarahodov/roboport/actions/runs/40495144700) ~579,6 с на шаг установки. Логи APT показывают, что пакеты системных шрифтов и библиотек скачивались с `azure.archive.ubuntu.com` со скоростью ~46,6–46,7 КБ/с (21,1–25,9 МБ в 7–9 минут). Сам Chromium из CDN скачался за секунды. Это проблема сетевой загрузки ОС-зависимостей на cold runner, а не скорости Chromium. Граница доверия к кэшам: GitHub `pull_request` сохраняет browser cache под `refs/pull/.../merge`, кэш из PR не виден в `main` или соседнем PR, даже при одном и том же ключе.

Для устранения повторного APT CI browser job использует **официальный** `mcr.microsoft.com/playwright:v1.56.1-noble`, соответствующий точной версии `@playwright/test=1.56.1`. В нём уже находятся браузеры и их системные библиотеки. Шаг `Verify pinned Chromium and operating system dependencies` **реально запускает и завершает** headless Chromium перед обязательными E2E и Docsify-тестами; неизвестные/mismatched image не принимаются тестом политики `tests/bootstrap/test_ci_workflow_policy.py`. Кэширование Chromium на runner удалено, поскольку в контейнере оно избыточно. npm cache по lockfile сохранён.

**Безопасность:** контейнер предназначен только для синтетических внутренних E2E, без секретов и внешних недоверенных сайтов. Образ ограничен версией; обновлять `@playwright/test` и образ синхронно. Проводить отдельный security review контейнерного запуска на root перед использованием с недоверенным контентом. Запуск `--with-deps` на host остается необходимым: наличие библиотек в контейнере не подтверждает их наличие на произвольном ноутбуке.

**Checkpoint подтверждён:** [PR #150](https://github.com/askarahodov/roboport/pull/150), [PR CI 40499075107](https://github.com/askarahodov/roboport/actions/runs/40499075107) — 4/4 GREEN, browser job ~101,7 с (первый image pull ~26 с). [Main CI 40499418679](https://github.com/askarahodov/roboport/actions/runs/40499418679) на `a42acda76e1a2ca0e7b8f7f330ef9cb162edcf06` — 4/4 GREEN, browser job ~108,9 с; нет скачивания apt-пакетов, browser smoke и E2E+Docsify зелёные. При проблемах образа/скачивания откатить изменение `container:` и вернуть шаги `actions/cache@v4` + `npx playwright install --with-deps chromium` из PR #148. Path-based пропуск browser checks **не включён**, пока не доказана корректная матрица влияния backend/контрактов/UX.

## Историческая проверка Windows CMD (#135)

До решения Linux-only отдельный `windows-cmd` на Windows runner проверял frontend build, TypeScript, ссылки и Docsify Chromium smoke. Это не было серверной проверкой. С 26.11.2026 обязательный Windows job выведен из scope: те же функциональные проверки выполняют Linux jobs `docs`, `api`, `web`, `browser-e2e`; полный PostgreSQL и browser gate сохранён. Исторические результаты CI остаются неизменными, не считать их новым Linux-only доказательством.

Откат при необходимости — обычный проверенный PR, согласованно возвращающий workflow, impact selector и release verifier; не отключать Linux тесты. Настройки branch protection должны соответствовать действующим обязательным checks; не обходить отсутствующую проверку.

## CI после оптимизации: выбор проверок по риску PR (18.11.2026)

**Полный release gate на `main` и при `workflow_dispatch` НЕ ослабляется:** все шесть Linux jobs (`impact`, `docs`, `postgres`, `api`, `web`, `browser-e2e`) обязаны успешно завершиться. PG17 выполняет полный integration suite, Alembic downgrade/re-upgrade и архивный backup/restore. Проверка `scripts/check_auto_release_ci.py` по-прежнему отвергает пропущенные обязательные jobs для release SHA.

**Для PR** отдельный быстрый `impact` job проверяет правила (`tests/bootstrap/test_ci_impact.py`) и по `merge-base...HEAD` выбирает нужные jobs; независимая проверка `docs` (ссылки и AutoDealer GAP) запускается всегда. Неизвестные пути, CI, инфраструктура, скрипты, контракты, миграции и критичный auth/ACL/identity/core код запускают **все четыре** scoped jobs (плюс impact/docs). Без безопасно вычислимого diff решение — полный CI, а не пропуск.

- Только `docs/**` / Markdown → `impact` + `docs`; тяжёлые jobs отмечаются skipped.
- Локальные `auto_*.py` + изменённые integration tests → `api` + `postgres` со scoped PG17 тестами; перед тестами обязательно выполняется Alembic upgrade. Если тесты не изменены, выполняется полный набор integration tests, чтобы не считать незакреплённое поведение проверенным.
- `apps/web/src/**/*.css` и статические изображения → `web`; изменение исполняемого UI → `web` + `browser-e2e`; изменения в E2E → `browser-e2e`.
- Общие frontend конфигурации и зависимости → `web` и `browser-e2e`. Изменение миграций/прав/CI → все jobs, включая полный PG rollback и restore.

Для проверки классификатора локально: `python -m unittest discover -s tests/bootstrap -p test_ci_impact.py`. Для ручного полного GitHub запуска используйте `workflow_dispatch`; эти настройки не заменяют независимые проверки #76 или legal/security/release #119. При ложном исключении области — расширить `scripts/ci_impact.py` и тесты либо запустить полный workflow, а не объявлять skipped-проверки успешными испытаниями.


## Выбор Playwright-сценариев внутри browser job PR (18.11.2026)

Дополнение к impact-based jobs: ранее даже изменение одного `apps/web/e2e/auto-order-register.spec.ts` запускало **весь** набор браузерных E2E. Теперь job `browser-e2e` остаётся обязательным при затронутом браузере, но в **доверенном узком PR** получает канонический ограниченный список путей от хостового `impact` job (полный checkout, diff от merge-base до HEAD), переданный в `browser_paths_b64`. Сам контейнер Playwright **не имеет `.git`**, поэтому никогда не вычисляет Git diff напрямую. `python -m scripts.ci_browser --paths-base64 <value> --run` выбирает точные сценарии. При недостоверном/слишком длинном списке, отсутствии тестового файла или ошибке его декодирования используется **полный E2E**. Если diff не удаётся вычислить в хостовом `impact`, тот job падает: CI не возвращает ложного зелёного результата. Git в браузерном контейнере для выбора тестов не используется.

- Изменился **только самостоятельный** `apps/web/e2e/*.spec.ts` (+ документы) → выполняется этот файл. Новые, переименованные/удалённые, неизвестные или отсутствующие тесты → **полный** Playwright.
- Изменён **известный изолированный** `AutoWorkOrderRegister.tsx` / `AutoWorkOrderWorkspace.tsx` / `AutoOrderServices.tsx` → запускаются заранее закреплённые соседние браузерные сценарии, включая реестр → рабочее место → услуги. Сопоставление находится в `scripts/ci_browser.py` и обязано совпадать с реально существующими `.spec.ts`; если нет — полный Playwright.
- Любой общий React router, общие компоненты, конфигурация, контракты, API/БД, миграции, нестандартный frontend путь, изменённый helper, инфраструктура либо нераспознанный файл **в том же PR** → полный Playwright. Не подменять это сканированием имени файла без подтверждённой зависимости. Изменения backend `auto_*.py` сами по себе по прежней политике выбирают PostgreSQL+API; если они **в сочетании с UI**, scoped browser отключается и выполняется полный тестовый набор.
- **Каждый push в `main` и `workflow_dispatch` безусловно запускает все E2E**, плюс Docsify navigation/search; выбор по PR здесь игнорируется. Сам Docsify smoke в browser job также **выполняется полностью при любом активном browser job**, даже если E2E выбраны точечно. Release verifier требует полный текущий Linux gate.
- Неизвестная область и любое изменение самого CI classifier/тестовой инфраструктуры → полный E2E. Все селекторы проверяются в `tests/bootstrap/test_ci_browser.py` и `test_ci_workflow_policy.py`.

**Локальная проверка плана:** `python -m scripts.ci_browser --base <base-sha>`; с `--run` требуется установленный Node и доступный synthetic FastAPI для браузерного workflow. Принудительный полный запуск: `python -m scripts.ci_browser --full --run`. Из корня репозитория отдельно: `python -m unittest discover -s tests/bootstrap -p test_ci_browser.py`.

**Критичное ограничение:** этот shortcut — только оптимизация скорости PR, не доказательство всей регрессии. Без замеров на реальных scoped PR нельзя обещать конкретное ускорение. Если появится зависимость между экраном и иным E2E, расширить явное правило или вернуть full; при признаках regression — использовать полный ручной workflow. Release-gate #119 всегда требует все обязательные job успешными на конкретном main SHA. Откат — revert `scripts/ci_browser.py`, browser step в `.github/workflows/ci.yml` и тестов; безопасный fallback всего browser suite предусмотрен по умолчанию.


### Пакетная проверка изолированных экранов отчётности (24.11.2026)

Базовый замер: [main CI 45372016055](https://github.com/askarahodov/roboport/actions/runs/45372016055), SHA `71e75b18512340ac46c4f52b0892963ec318b2f2`, 7/7 jobs SUCCESS. По времени jobs: browser-e2e 365 с (автомобильные E2E 271 с, Docsify 32 с), PostgreSQL 249 с (integration 209 с), web 97 с, Windows 100 с, API 42 с, docs 10 с, impact 7 с. Это один baseline, не гарантированное время следующих прогонов.

Для локальных изменений **только** известных автономных React-экранов (без общего router, shared-кода, API, ACL, миграций, конфигурации и тестовых helper) селектор запускает объединение явных групп Playwright: кассовая книга/ДДС/касса; закрытые наряды/первичка материалов/финансовая сверка и переходы реестра/рабочего места; выработка/смены/назначения; ABC/XYZ и основные складские сценарии; CRM-воронка/результаты/канбан/обращения. Группы содержат фактические `.spec.ts` из репозитория и проверяются bootstrap-тестами; при отсутствии любого ожидаемого сценария выполняется полный набор. Изменение нескольких известных экранов не запускает один сценарий дважды. Любой неизвестный или общий путь в этом PR требует полного browser suite. Docsify, требуемые jobs и полный main/manual/release gate сохранены.

Переименование файла учитывается как удаление старого и добавление нового пути (Git diff `--no-renames`): перенос критического файла в docs не должен скрыть его прежний путь от impact. Регрессия проверяется на временном Git-репозитории. Для отката достаточно revert пакета CI-селекторов; бизнес-данные и схема БД не изменяются. Фактическое ускорение scoped PR измеряется отдельно после появления соответствующего PR, а не выводится из количества выбранных сценариев.


### Изолированные offline-пакеты миграции: экономия без пропуска PG-защиты (24.11.2026)

Для **PR только с известными автономными инструментами** `scripts/ci_impact.py` допускает четыре закрытые группы: provenance manifest, mapping VEHICLE CSV, совместный XLSX converter/batch, trusted cutover export verifier + его внутренний export модуль. Перед применением shortcut проверяются существование всех перечисленных source/contract/integration файлов и буквальные ссылки на имена модулей в **обоих** Git-деревьях (base и HEAD). Неизвестный исполняемый потребитель, отсутствующий тест, смешанные изменения, ошибки Git или неизвестные скрипты — полный CI. Это консервативный вручную проверенный список, не универсальный статический анализатор Python; новые зависимости должны расширять правило либо возвращать full.

- Первые три группы не выполняют SQL/HTTP и запускают полный API/contract job, включая их contract tests, плюс обязательные impact/docs.
- Trusted cutover export (CLI/внутренний модуль) дополнительно **всегда** запускает `tests/integration/test_pg_auto_synthetic_cutover_export.py` на PG17 после Alembic upgrade, даже если сам тестовый файл не менялся. Не заменяет ACL/transaction/negative PG доказательство.
- Общие preflight/DB import helpers, схемы, ACL/HTTP, restore probes, CI selectors и смешанные неизвестные изменения не входят в shortcut. Полный main/manual gate и `check_auto_release_ci.py` сохранены.
- Неизвестные потребители проверяются и в старом дереве, чтобы удаление/переименование ссылки не скрыло необходимость полного прогона. Отдельные unit tests используют временный настоящий Git-репозиторий, missing test/ref, новый и старый consumer и mandatory PG selection.

Фактическая экономия измеряется на последующих подходящих PR; skipped не PASS. Для отката — revert только selector/tests/docs, без изменения данных. До зелёного CI на изменении самого selector действует прежняя политика.
