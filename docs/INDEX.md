# Карта документации ROBOport

**Вся смысловая документация проекта пишется по-русски**. Допустимые исключения — язык программирования, стандартные названия API/SQL/HTTP, английские поля/параметры и команды инструментов. Не дублируйте одно и то же правило в нескольких независимых документах.

| Документ | Что определяет | Читатели |
|---|---|---|
| [`TECHNICAL_SPEC.md`](../TECHNICAL_SPEC.md) | Все функции, инварианты, границы MVP | Все агенты |
| [`REPOSITORY_STRUCTURE.md`](REPOSITORY_STRUCTURE.md) | Дерево репозитория, владение кодом | Оркестратор, разработчики |
| [`TECH_STACK.md`](TECH_STACK.md) | Стек, версии, отложенные технологии | Архитектор, DevOps |
| [`architecture/ADR.md`](architecture/ADR.md) | Технические решения и их статус | Все |
| [`architecture/BOUNDARIES.md`](architecture/BOUNDARIES.md) | Границы модулей | Backend, БД |
| [`data/DATA_MODEL.md`](data/DATA_MODEL.md) | Сущности и ограничения | Backend, БД |
| [`domain/TICKET_FSM.md`](domain/TICKET_FSM.md) | Состояния заявки и guards | Backend, QA |
| [`api/API_IMPLEMENTATION.md`](api/API_IMPLEMENTATION.md) | Точность OpenAPI/HTTP | Backend, Frontend |
| [`security/SECURITY_MODEL.md`](security/SECURITY_MODEL.md) | RBAC/ACL/ABAC и негативные сценарии | Backend, Security |
| [`security/LOCAL_AUTH_AND_MINIO.md`](security/LOCAL_AUTH_AND_MINIO.md) | Локальная аутентификация, сессии, ACL и приватный MinIO в dev | Backend, DevOps, AI-агенты |
| [`design/PRELIMINARY_BRANDBOOK.md`](design/PRELIMINARY_BRANDBOOK.md) | Разработческий брендбук и две темы | Design, Frontend |
| [`implementation/WORK_BREAKDOWN.md`](implementation/WORK_BREAKDOWN.md) | Список GitHub Issues и зависимости | Оркестратор |
| [`implementation/ISSUE_STANDARD.md`](implementation/ISSUE_STANDARD.md) | Обязательная структура каждой задачи | Все |
| [`implementation/DEVELOPMENT_ROADMAP.md`](implementation/DEVELOPMENT_ROADMAP.md) | Очередность и этапы | Оркестратор |
| [`quality/ACCEPTANCE_TESTS.md`](quality/ACCEPTANCE_TESTS.md) | Приёмочные и негативные тесты | QA |
| [`operations/DEV_ENVIRONMENT.md`](operations/DEV_ENVIRONMENT.md) | Команды и dev-окружение | Все |
| [`operations/SECURITY_RELEASE_GATES.md`](operations/SECURITY_RELEASE_GATES.md) | Порог разработки и публичного запуска | Владелец |

## Порядок чтения для новой AI-задачи

`GitHub Issue → AGENTS.md → TECHNICAL_SPEC.md → релевантные ADR/FSM/API/DB документы → тесты → код`. Если Issue требует неописанного решения, записать отдельное ADR/блокер, а не додумывать принципиальные правила.
