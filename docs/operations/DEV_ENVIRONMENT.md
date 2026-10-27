# Окружение разработки ROBOport / Airmatik

**Статус:** current для синтетического robot-domain dev стенда; авто-функции пока строятся по этапам. Раздел не даёт разрешения на production. Дата сверки: 26.10.2026.

## Требования

Docker Engine + Compose v2, Python 3.13, Node.js 24, npm и Git. Для полноценного dev требуется одноразовый PostgreSQL 17 и приватный MinIO, при OIDC — Keycloak 26.3.3. Синтетический локальный стенд описан в [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md).

## Быстрые проверки (Linux/macOS с shell)

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

## Windows

В Git Bash/WSL2 можно следовать shell-инструкциям выше. При запрещённом PowerShell и наличии только **CMD** для работы с Compose допустима команда:

```cmd
docker compose --env-file infra/dev/.env.local -f infra/dev/compose.local.yml ps
```

Но `bootstrap-local.sh` и `start-https.sh` **не запускаются нативным CMD**. Поэтому простую команду `docker compose up` нельзя выдавать за полный рабочий локальный API/WEB+Keycloak/OIDC; воспроизводимый CMD-only bootstrap ещё необходимо реализовать и проверить по #122/#119. Не обещать успешный native Windows запуск без прогона.

## Тесты и безопасные операции

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

Из **Windows CMD**, без PowerShell (при установленном Python 3 с командой `python`):

```cmd
cd apps\web
npm ci
npx playwright install chromium
npm run test:docs:e2e
```

В конфигурации Docsify Playwright локальный HTTP server запускается через `python -m http.server`, который подходит для Linux и Windows при доступном `python`. Для полноценного `npm run test:e2e` необходим запущенный synthetic FastAPI на `127.0.0.1:8000` с тестовыми зависимостями; запускать без production данных. CMD-команды предназначены для локальной проверки, независимый прогон на Windows всё ещё требуется; зелёный Linux CI сам по себе не доказывает Windows-совместимость.


## Текущая browser CI-конфигурация на официальном Playwright image (#135)

**Root cause cold-run:** [main CI 40494924138](https://github.com/askarahodov/roboport/actions/runs/40494924138) потратил ~469,6 с, а [PR CI 40495144700](https://github.com/askarahodov/roboport/actions/runs/40495144700) ~579,6 с на шаг установки. Логи APT показывают, что пакеты системных шрифтов и библиотек скачивались с `azure.archive.ubuntu.com` со скоростью ~46,6–46,7 КБ/с (21,1–25,9 МБ в 7–9 минут). Сам Chromium из CDN скачался за секунды. Это проблема сетевой загрузки ОС-зависимостей на cold runner, а не скорости Chromium. Граница доверия к кэшам: GitHub `pull_request` сохраняет browser cache под `refs/pull/.../merge`, кэш из PR не виден в `main` или соседнем PR, даже при одном и том же ключе.

Для устранения повторного APT CI browser job использует **официальный** `mcr.microsoft.com/playwright:v1.56.1-noble`, соответствующий точной версии `@playwright/test=1.56.1`. В нём уже находятся браузеры и их системные библиотеки. Шаг `Verify pinned Chromium and operating system dependencies` **реально запускает и завершает** headless Chromium перед обязательными E2E и Docsify-тестами; неизвестные/mismatched image не принимаются тестом политики `tests/bootstrap/test_ci_workflow_policy.py`. Кэширование Chromium на runner удалено, поскольку в контейнере оно избыточно. npm cache по lockfile сохранён.

**Безопасность:** контейнер предназначен только для синтетических внутренних E2E, без секретов и внешних недоверенных сайтов. Образ ограничен версией; обновлять `@playwright/test` и образ синхронно. Проводить отдельный security review контейнерного запуска на root перед использованием с недоверенным контентом. Запуск `--with-deps` на host остается необходимым: наличие библиотек в контейнере не подтверждает их наличие на произвольном ноутбуке.

**Checkpoint подтверждён:** [PR #150](https://github.com/askarahodov/roboport/pull/150), [PR CI 40499075107](https://github.com/askarahodov/roboport/actions/runs/40499075107) — 4/4 GREEN, browser job ~101,7 с (первый image pull ~26 с). [Main CI 40499418679](https://github.com/askarahodov/roboport/actions/runs/40499418679) на `a42acda76e1a2ca0e7b8f7f330ef9cb162edcf06` — 4/4 GREEN, browser job ~108,9 с; нет скачивания apt-пакетов, browser smoke и E2E+Docsify зелёные. При проблемах образа/скачивания откатить изменение `container:` и вернуть шаги `actions/cache@v4` + `npx playwright install --with-deps chromium` из PR #148. Path-based пропуск browser checks **не включён**, пока не доказана корректная матрица влияния backend/контрактов/UX.

## CI #135 — подтверждение native Windows CMD и безопасный объём browser-проверок

**Текущая политика:** `.github/workflows/ci.yml` проверяет `postgres`, `api`, `web` и `browser-e2e` **для каждого PR и push в main без path-based skip**, поскольку изменение FastAPI, контракта, маршрутов, миграций или документации может повлиять на E2E/Docsify. Без полного dependency graph невозможно безопасно сделать выбор по `apps/web/**`; вместо потенциально ложного зелёного статуса оставлена обязательная проверка. `concurrency.cancel-in-progress` отменяет устаревшие запуски одной PR-ветки, не выключая проверку нового commit SHA.

Дополнительно отдельный job `windows-cmd` запускается на **реальном GitHub-hosted `windows-latest` runner**, со штатным `shell: cmd` и установленными Python 3.13 / Node.js 24. Он проверяет внутренние ссылки документации, выполняет `npm ci`, генерацию TypeScript-контракта и `npm run typecheck`, настоящую frontend-сборку, установку pinned Chromium для Windows и Playwright Docsify navigation/search smoke. Скрипты PowerShell (`.ps1`) и WSL здесь не используются. Screenshots/trace публикуются как `roboport-windows-cmd-evidence` даже при красном тесте. Этот job является **обязательной пятой** CI-проверкой и не заменяет более полные Linux/PostgreSQL/автомобильные E2E.

Те же команды локально из корня репозитория, **нативный Windows CMD** (без PowerShell, в `PATH` Python 3.13/Node.js 24):

```cmd
python scripts\check_docs_links.py
cd apps\web
npm ci --no-audit --no-fund
npm run typecheck
npm run build
npx playwright install chromium
npm run test:docs:e2e
```

Это доказательство совместимости **frontend и Docsify** на Windows, **не** доказательство полного локального bootstrap контейнеров, HTTPS, PostgreSQL 17, Keycloak/OIDC или MinIO под CMD. Скрипт `infra/dev/bootstrap-local.sh` требует Unix shell; его нативная замена остаётся #122/#119. Реальные client/VIN/PII и production-credentials для smoke запрещены.

**Benchmark/rollback:** производительность устранения холодных `apt` задержек подтверждена в PR #150: вместо 469–580 секунд **только на browser install** Linux container browser job ~102–109 секунд **целиком** (конкретные GitHub-run ссылки и caveats выше). При проблеме с Windows CMD приоритет — исправить реальную несовместимость, а не `continue-on-error` или пропуск. Для точечного отката Windows job допустим revert PR после фиксации причины; для Linux Chromium — документированный rollback к host-runner из PR #148 с `npx playwright install --with-deps chromium`, сохраняя полные E2E и Docsify.
