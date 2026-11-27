# Gitflow: одна интеграционная ветка, контролируемая очередь и чистые refs

Решение заказчика 27.11.2026. Применяется к askarahodov/roboport. Это облегчённый Gitflow на базе trunk-based development, а не классическая схема с долгоживущими develop/release. [AGENTS](../../AGENTS.md) и [регламент агента](AUTONOMOUS_AGENT_WORKFLOW.md) обязательны.

## Жизненный цикл

| Шаг | Правило |
|---|---|
| Triage | В начале сессии получить актуальный main SHA/CI, open PR включая draft, heads/bases, review и unresolved threads, список refs. |
| Ready | WIP ≤ 2 продуктовых PR на весь репозиторий, ≤ 1 на агента. Если лимит достигнут, сначала завершить имеющееся. Один исключительный служебный PR для устранения общего блокера CI/security/регламента, с обоснованием. |
| Branch | Ветка от свежего green main: feature/issue-N-short-name, fix/issue-N-short-name либо chore/short-name. Не создавать develop, не переиспользовать завершённую ветку. |
| Implement | Один связанный функциональный пакет, тесты, контракт, документация; Refs #N. |
| Review | CI на точном HEAD, независимый code/security review по применимости, отсутствие нерешённых тредов. Красный PR чинить в его ветке. Не запрашивать тот же review бесконечно; эскалировать недоступный gate. |
| Integrate | Порядок зависимостей, затем возраст готового PR. Сверить base, HEAD и green main; merge через PR, не прямой push. Один merge, затем полный green main CI прежде следующего. |
| Complete | После main CI сверить AC/DoD, закрыть исходную Issue только при полном выполнении. Проверить cleanup; сохранить доказательства и перейти к следующей задаче только при свободном WIP. |

При конфликте/красном main — остановить merge. Не закрывать полезные PR ради WIP и не менять draft для обхода лимита. Не включать автоматическое слияние и не заменять независимое Security Review авторским review. При внешнем блокере записать статус, точный SHA, недостающий gate, ответственного или канал получения решения в PR / #119. Зелёные тесты сами по себе не означают разрешение merge.

## Уборка веток

Workflow [branch-hygiene.yml](../../.github/workflows/branch-hygiene.yml) запускает автоматическую уборку после завершения успешного CI main; еженедельный аудит и ручной dry-run показывают кандидатов и причины сохранения. Проверяет фактический текущий main и все шесть обязательных jobs на его точном HEAD. Действие разрешено лишь для временной feature/fix/chore/tmp ветки этого repo с точным merged PR head и доказанным merge SHA — предком актуального main. Ветка, используемая открытым PR (head или base), защищённая, main/default, release/hotfix, форк или ветка без доказанного merge, сохраняется. Перед удалением повторно читаются PR, branch и main; Git push deletion с точным force-with-lease SHA не удаляет ветку при конкурентном push. GitHub не предоставляет единую атомарную транзакцию с созданием PR; не открывать новый PR от завершённой ветки, использовать новый ref. Ошибки прав/lease явно отражаются в отчёте. Ручной режим удаления требует dry_run=false, но не обходит ни один guard.

Для orphan / closed-unmerged / legacy refs общего автоматического удаления нет. Исключение — явно перечисленные семь проверенных SHA по разовой инвентаризации ниже, уже достижимых из main; все остальные guards остаются обязательными. Сначала проверить PR/Issue, diff/ancestry, зависимости, владельца, уникальные коммиты и disposition; задокументировать полный SHA, безопасную ссылку/архив при необходимости и явное решение. Отдельные legacy ветки из [LEGACY_PR_DISPOSITION](../auto/LEGACY_PR_DISPOSITION.md) сохраняются до разрешённого decommission. Не удалять пользовательские данные, теги и релизные refs. Политика не меняет production IAM или полномочия #119.

## Начальная очередь на 27.11.2026

На момент сверки: main bb3e1152ecc8571dd10e42426ea6f5fe22371d2d, CI [45815133827](https://github.com/askarahodov/roboport/actions/runs/45815133827) success; 43 удалённые ветки, 8 open PR (#617, #627–#633). Независимый обычный Codex review на всех восьми точных heads без findings; отдельный Security Review не зарегистрирован. Причина накопления — недоступный отдельный gate и отсутствие WIP-ограничения. До его решения не открывать новые продуктовые PR, существующие не закрывать и не удалять их branches. Служебный PR с этим регламентом — исключение для восстановления порядка. Автоматизация вступает в силу только после merge; текущее состояние не является доказательством удаления веток.

## Разовая инвентаризация и разрешённая очистка

Сверка 27.11.2026: 44 refs после служебной ветки; main + 9 открытых PR + 34 неактивных refs. Для всех 34 выполнен GitHub compare `bb3e1152ecc8571dd10e42426ea6f5fe22371d2d...HEAD`. У семи `ahead_by=0`, `status=behind`, то есть все их коммиты уже достижимы из main. Точное имя/SHA разрешено удалить по поручению заказчика, но только после действующих guards. Это не удаление истории коммитов и не закрытие связанных Issues. У остальных 27 `diverged`, уникальные коммиты; сохраняются для отдельной сверки/архивирования. PR #634 остаётся единственным служебным PR, новых продуктовых веток не создавать.

| Ветка | Проверенный SHA | Решение |
|---|---|---|
| `feature/issue-130-safe-vehicle-location-transfer` | `e2f13430d91326723fe468a1224c92dbec063b96` | DELETE после guards |
| `feature/issue-444-crm-kanban-consent-outbox` | `00010094c0ba47d3b04d239f1f32c5b75654ffae` | DELETE после guards |
| `feature/issue-448-technical-workshop-report` | `87d8bcb319fc41960b32a8fd570c58d4402b22ac` | DELETE после guards |
| `feature/issue-undefined-dossier-png-validation` | `dbd1250e25f12f1f096590bf898aa5c600d7f2c5` | DELETE после guards |
| `feature/issue-undefined-mechanic-offer-withdrawal` | `59da77b6ad8347d88b837af7b5d3b6bf5cbd38fc` | DELETE после guards |
| `fix/issue-168-receiving-status-e2e` | `6bf8f1f3c4e225f7d92814ef15bc3ae23c7ac5f1` | DELETE после guards |
| `tmp/168-serial-clean-273` | `3e28386da295786b4660dd8794cb44af3185110d` | DELETE после guards |

Остальные 27 не удалены: `bootstrap/ai-development-spec-v1`, feature issue-11, 17 (две), 18, 21, 23 (две), 25, 27, 28, 108, 113, 130 (registry/review), 165, 169 (completion/followup/synthetic), 173, 174, 441, 448 (internal/serial), 168 (две), fix/issue-169-crm-review-findings. Доказательство ancestry не устанавливает семантического равенства diverged веток. Отдельное архивирование или удаление требует disposition, не безусловной очистки.
