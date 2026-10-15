# Структура репозитория ROBOport

**Принцип:** один репозиторий (**monorepo**), два приложения и самостоятельный пакет контрактов/дизайна. Декомпозиция по доменным модулям, а не по случайным типам файлов. Микросервисы не нужны на первом этапе.

## Текущее дерево и согласованное расширение

```text
roboport/
├── README.md                      # запуск и состояние
├── TECHNICAL_SPEC.md             # единственное полное ТЗ
├── AGENTS.md                      # инструкции всем AI-агентам
├── CONTRIBUTING.md               # правила PR
├── SECURITY.md                   # ответственная работа с данными
├── .github/
│   ├── workflows/ci.yml          # build/tests; ещё не подтверждён зелёный прогон
│   └── ISSUE_TEMPLATE/           # создать позднее при настройке проекта
├── apps/
│   ├── api/                      # FastAPI backend
│   │   ├── pyproject.toml
│   │   ├── roboport_api/
│   │   │   ├── main.py           # composition root
│   │   │   ├── catalog.py        # существующий демонстрационный read-only каталог
│   │   │   ├── core/             # целевое: конфигурация, ошибки, auth, DI
│   │   │   ├── modules/          # целевое: бизнес-модули
│   │   │   │   ├── catalog/
│   │   │   │   ├── identity/       # локальные пользователи, сессии
│   │   │   │   ├── authorization/  # RBAC+ACL, объектные политики
│   │   │   │   ├── customers/
│   │   │   │   ├── tickets/
│   │   │   │   ├── estimates/
│   │   │   │   ├── compatibility/
│   │   │   │   ├── inventory/
│   │   │   │   ├── quality/
│   │   │   │   ├── documents/
│   │   │   │   ├── notifications/
│   │   │   │   └── audit/
│   │   │   └── workers/          # целевое: outbox worker
│   │   └── alembic/              # целевое: новая ревизионная история
│   └── web/                      # React/TypeScript/Vite
│       ├── package.json
│       ├── package-lock.json      # создать и зафиксировать до npm ci
│       ├── src/
│       │   ├── main.tsx
│       │   ├── app/               # роутер, providers, общая оболочка
│       │   ├── pages/public/      # сайт/каталог
│       │   ├── pages/customer/    # обязательный кабинет
│       │   ├── pages/staff/       # CRM
│       │   ├── features/          # логика конкретного сценария
│       │   ├── shared/ui/         # универсальные компоненты
│       │   ├── shared/api/        # типизированный API-клиент
│       │   └── styles/            # дизайн-токены
│       └── public/               # оригинальный логотип C
├── contracts/
│   └── openapi.yaml              # проектный API, не флаг готовности реализации
├── db/
│   └── legacy_drafts/            # шесть неисполняемых исторических SQL
├── design/
│   ├── brand/                    # оригинальная угловая R, SVG
│   ├── tokens/                   # Light/Dark/Auto JSON+CSS
│   ├── prototype/                # демонстрационные UI-макеты
│   └── reference/                # правила применения знака/состояний
├── docs/                         # вся смысловая документация по-русски
│   ├── architecture/ api/ data/ domain/ design/
│   ├── implementation/ operations/ quality/ security/
│   ├── INDEX.md
│   ├── REPOSITORY_STRUCTURE.md
│   └── TECH_STACK.md
├── tests/
│   ├── bootstrap/               # текущие тесты FastAPI/контракта
│   ├── unit/                    # целевое
│   ├── integration/             # целевое: disposable PostgreSQL/MinIO
│   ├── contract/                # целевое: OpenAPI 2xx/4xx
│   ├── e2e/                     # целевое: Playwright
│   └── security/                # целевое: BOLA, отзыв прав, fail-closed
├── scripts/                       # повторяемые команды/проверки
└── infra/dev/                    # целевое: compose с test-only PostgreSQL и MinIO
```

**Важно:** обозначенные «целевое» каталоги не выдаются за уже созданные. Агент добавляет их в рамках конкретной GitHub Issue с тестом и без пустых папок.

## Правила владения

- `apps/api/modules/<домен>` владеет своими моделями, use-cases и репозиториями; нельзя обходить доменные guards прямыми SQL-запросами из API роутов.
- `apps/web/pages/*` не содержит авторизационных решений: frontend показывает состояние, сервер проверяет объект и роль.
- `contracts/openapi.yaml` — обязательная точка синхронизации при изменении HTTP; реализованный endpoint должен иметь HTTP tests.
- `tests/integration` проверяет реальную одноразовую БД, `tests/e2e` — браузер и тестовый сервер, не картинки как замену тестов.
- `design/brand` — только оригинальная угловая буква R концепции C. Изображения/шрифты с непроверенными правами в публичный репозиторий не добавлять.
- `db/legacy_drafts` никогда не считать исполненными миграциями; новый `alembic/` должен начинаться после анализа конфликтов.

## Правило изменения структуры

Новый крупный модуль, пакет или инфраструктура → ADR в том же PR. Существующий модуль расширять по задаче, не создавать дублирующий стек. Первый приоритет — воспроизводимые проверки и небольшие независимые PR.
