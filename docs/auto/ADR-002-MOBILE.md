# ADR-AUTO-002: клиентское мобильное приложение и граница native auth

**Дата:** 26.11.2026 · **Статус: proposed / planned, требуется архитектурное и security review.**
Связано с #117, #118, #131, #119, #92 и [ADR-AUTO-001](ADR-001.md).
База анализа: `cf853cf075ef84bcfd4d5677b810cb344f1b1fd0`. Документ не разрешает публикацию, не создаёт клиент OAuth или новый endpoint, не утверждает production identity.

## Контекст и варианты

Цель — клиент СТО для iOS/Android, а не второй сервер. Текущий стек: React/TypeScript, FastAPI, PostgreSQL 17. Существующий кабинет использует живую cookie-сессию, CSRF и объектные проверки; `session_service.py` выдаёт opaque session, хранит digest, имеет idle/absolute expiry и revoke. Это не native-token service. Частные OpenAPI ограничены HTTPS loopback и synthetic-only; опубликованный автомобильный API и native auth ещё planned. Не переносить dev cookies, заголовки доверенного proxy или dev flags в приложение.

| Вариант | Преимущество | Ограничение |
|---|---|---|
| React Native + Expo (development build) | TypeScript и React знакомы команде; можно переиспользовать типы, чистые валидаторы и semantic tokens | Нужны отдельные native UI, платформенная проверка OAuth/deep links/secure storage, сборки и review |
| Flutter | Единая native UI модель, независимый от web слой | Новый язык Dart, отдельный toolchain и DTO-генерация; больше нового кода для этой команды |
| PWA | Переиспользование текущего responsive web, не нужен новый native release pipeline | Не доказывает native auth, device lifecycle и store delivery; платформенные ограничения проверяются отдельно |

**Предлагаемое решение:** React Native + Expo как кандидат для ограниченного MVP после review; responsive web остаётся основным действующим каналом. Flutter не исключён при провале platform spike. PWA — возможный web-канал, но не замена приёмке native. Не устанавливать зависимости до проверки поддерживаемых версий, лицензий, supply chain, стоимости сборки и platform capability на обоих устройствах. Предпочесть локальные/контролируемые сборки; внешние облачные сервисы и аккаунты не включать автоматически.

## Архитектурные границы

`Native UI → use-case orchestration → typed API transport → /api/v1/auto → shared application services → PostgreSQL`.

- Сервер владеет всеми переходами, ценами, суммами, доступом, idempotency, аудитом, tenant/branch/owner scope; клиент не принимает бизнес-решения, не получает ключи поставщиков.
- Возможный общий TS слой: сгенерированные DTO и чистые валидаторы без DOM/fetch/cookie/AsyncStorage. Не импортировать текущий browser transport в native. Разделение transport/auth и UI; не создавать второй backend.
- Новый публичный контракт должен быть отдельно проверен и опубликован только после #119. Не механически менять `/dev/auto` на `/auto`. Версионирование, ошибки, pagination, exact-retry и no-store согласовать с web; любое изменение существующего контракта проверять на совместимость.
- AuthN (проверка токена и server session) отделена от AuthZ (живые role/customer link/object grants). Bearer-контекст передаётся общим сервисам через типизированный principal, не подставляется в cookie/CSRF. Web cookie + CSRF остаются неизменными. При конфликте двух credentials запрос отклоняется; native path не принимает cookie как fallback.

## Контрактная карта MVP (все native экраны planned)

| Экран / намерение | Действующее dev-доказательство | Требование к будущему API |
|---|---|---|
| Вход, выход, устройства | browser session, [архитектура](ARCHITECTURE.md) | native authorization code, refresh/revoke, список собственных сессий без token values |
| Мои авто, карточка | [auto-dev-openapi.yaml](../../contracts/auto-dev-openapi.yaml), [владение](VEHICLE_OWNERSHIP.md) | только verified non-revoked link, masked VIN, cursor связан с principal |
| Запись, перенос, отмена | [booking contract](../../contracts/auto-booking-dev-openapi.json), [запись](CUSTOMER_BOOKING.md) | только доступный филиал/пост, серверная конкуренция, exact idempotency key и authoritative receipt |
| История и статус | [customer orders contract](../../contracts/auto-customer-orders-dev-openapi.json), [история](CUSTOMER_HISTORY.md) | исходная customer link, не текущий VIN-владелец; стабильные 403/404 |
| Смета, решение, уточнение | тот же orders contract, [сметы](ESTIMATES.md) | точные order/revision/scope/суммы; pending clarification блокирует решение; повтор только того же payload/key |
| Документы | [ACL документов](DOCUMENT_ACCESS.md), [технический пакет](DEMO_CLOSE.md) | повторная авторизация каждой загрузки, no-store, digest где предусмотрен; real legal documents не обещать |
| Уведомления | [синтетические уведомления](APPOINTMENT_NOTIFICATIONS.md) | отдельно approved opt-in, device token binding, revoke и минимальный payload; без live push до credentials/#119 |

Публичные URI и DTO ещё не утверждены. За пределами MVP: оплата, фискализация, OEM, staff workflows, offline mutations, реальная отправка push. Они не становятся доступными через native-клиент.

## Предложение по native authorization (не реализация)

1. Отдельный OAuth/OIDC public client для каждой платформы и среды. Authorization Code + PKCE S256, криптографически случайные одноразовые state/nonce/verifier, системный браузер, точный allowlist redirect URI и проверка issuer. Не embedded WebView, не password grant, не implicit, не client secret в bundle. Предпочтительны verified HTTPS app/universal links с проверенной связью домена и приложения; при недоступной конфигурации авторизация закрыта.
2. Callback принимает только ожидаемый redirect текущей незавершённой транзакции. Ошибка state/nonce/issuer/redirect, повтор кода, смена аккаунта или истечение прерывают вход; code/verifier/token не попадают в логи, telemetry или deep-link history. ID token не используется как API credential.
3. API принимает access token только своего audience и issuer, с проверкой срока, подписи/валидности и соответствующей активной серверной device session. Выбор opaque token или JWT + online session check — отдельный security decision после spike; JWT claims сами по себе не дают объектных прав. Никакой offline validation без актуального server revoke.
4. Access token в памяти. Refresh credential в iOS Keychain / Android encrypted storage с ключом Keystore, device-only/no backup, не AsyncStorage, не clipboard, не URL. Один refresh in flight на сессию; rotation атомарна, повтор уже использованного refresh token отзывает family, включая текущий access. При потерянном ответе и неразрешённом состоянии — повторный login, не автоматический replay старого refresh. TTL, absolute/idle limits, DPoP/device binding и политика compromised devices требуют отдельного review; пока не согласованы, native auth не активируется.
5. Выход отзывает server session/family и уничтожает local credentials, state и данные. Если сети нет — немедленно очистить локально, явно сообщить, что удалённый revoke не подтверждён; управление устройствами из другого доверенного сеанса остаётся необходимым. При удалённом отзыве следующий запрос/foreground revalidation закрывает доступ; server authorization не ждёт окончания access-token TTL.
6. Биометрия только локально открывает защищённый credential после opt-in; не заменяет server auth, юридическое согласие или права. Изменение биометрии, restore/перенос устройства, повреждение storage → очистка и повторный вход. Не обещать, что secure storage исключает все атаки на скомпрометированном устройстве.

## Модель угроз и условия отказа

| Угроза | Предлагаемый guard | Проверяемое отрицательное условие |
|---|---|---|
| Перехват callback/code, login mix-up | PKCE, state, nonce, verified redirect и issuer | чужой redirect, code/state replay, неверный issuer → нет сессии |
| Кража refresh/access, гонка обновления | защищённое хранение, rotation, single-flight, live session check | два refresh одной версии, family reuse, expired/revoked token → нет доступа |
| Чужой объект/tenant, старый владелец | общие live ACL + original link; scope из сервера | A/B, смена филиала, отзыв роли/связи, новый владелец → 403/404, без истории |
| Прокси, MITM, dev ingress | HTTPS с штатной проверкой сертификата, запрет cleartext и dev endpoint в release build | invalid TLS/HTTP, dev flag или ошибочный host → соединение отвергнуто |
| Утечка из backup, screenshot, crash, push | no disk cache, минимальные события, скрытие task-switcher preview, generic push | после logout/revoke/restart нет sensitive cache; payload/logs без VIN, сумм, токенов и документов |
| Поддельный deep link и устаревшее согласие | allowlist routes, повторная загрузка после login, явное действие, server revision/key | ссылка не меняет состояние; outdated revision/duplicate → 409 или exact replay |
| Сбой update/компрометация пакета | pinned dependencies, signed builds, проверяемая provenance, rollback | несовместимая версия DTO и неизвестный update → безопасная ошибка, не обход ACL |

Не обещать абсолютную защиту screenshot/compromised OS. Скачанные пользователем копии нельзя гарантированно отозвать; до утверждённой политики экспорта native сохранение приватных файлов запрещено.

## Offline, жизненный цикл и ошибки UI

- MVP online-only для персональных/объектных данных: локально разрешены только тема, язык и несекретные настройки. Нет persistent cache заказов, авто, фото, смет, файлов, offline queue мутаций, service-worker cache и background prefetch с PII. Состояние mutation pending и requestKey — в памяти; после потери процесса перечитать серверное состояние, не повторять решение автоматически.
- При background закрыть sensitive screen snapshot по возможности платформы; при foreground, восстановлении сети и смене аккаунта скрыть прежние данные до повторной проверки session/object. При отсутствии сети или отказе revalidation показать нейтральный экран без старых данных. Не считать локальный timeout доказательством, что сервер отменил запись.
- 401 → очистка и вход (допустимо одно контролируемое refresh при валидном состоянии); 403/404 → немедленно убрать запрещённый объект; 409 → перечитать и попросить новое явное решение; 422 → исправить ввод; 429 → bounded backoff; 5xx/network → понятный retry чтения, без автоматического повтора изменённой мутации. Не раскрывать existence другого объекта.
- Push при отдельном разрешении содержит только нейтральное сообщение; открытие → login и fresh ACL; device token не равен пользовательской идентичности, unregister при выходе/смене аккаунта. Не отправлять настоящий push из dev.

## UX-прототип (текстовые flows, не работающие экраны)

1. Вход через системный браузер → нейтральный loading → «Мои автомобили» (маска VIN, пустое состояние, retry); отдельный путь «Войти снова».
2. Авто → «Записаться» → филиал/услуга/слот → явное подтверждение → receipt из сервера; конфликт слота возвращает к выбору. «Мои записи» → перенос/отмена с подтверждением.
3. «Заказ-наряды» → карточка с timeline и текущей версией сметы → состав и суммы → согласовать/отклонить/запросить уточнение. Не показывать доступное действие, пока DTO/ACL не проверены. Согласие не юридическая подпись.
4. Карточка → доступные технические документы; уведомление ведёт на свежую карточку, не открывает файл напрямую. Настройки → тема, приватность, устройства, выход.

Использовать [БоксРитм / Auto Digital](BRAND_GUIDE.md): Light/Dark/Auto, semantic colors, общий SVG/wordmark, native safe areas, dynamic type, VoiceOver/TalkBack, последовательный focus, touch targets по платформе, ошибки не только цветом. Внешние прототипы и screenshot evidence не созданы; требуются visual/design approval #92.

## План реализации и acceptance gates

| Этап | Acceptance / обязательные тесты | Статус |
|---|---|---|
| A. ADR + platform spike | Expo/Flutter/PWA review, iOS simulator и Android emulator: system-browser callback, verified links, protected storage, fail-closed on invalid link/TLS | ADR proposed; spike planned |
| B. Native auth + versioned contract (#117) | PG17/API: PKCE/issuer/audience, expiration, rotation race/reuse, revoke, account switch, cookie/CSRF regression; contract tests на обеих transport схемах | blocked до auth/security decision и согласованного identity |
| C. Read-only customer parity | A/B/tenant, revoked link, old owner, pagination, malformed DTO, foreground/airplane mode; device E2E обоих клиентов | зависит от B и #117 |
| D. Booking + quote decisions | 409 slot/revision, exact retry после потери ответа, clarification, cancel/revoke; PG17/HTTP + device E2E | зависит от C |
| E. Документы + opt-in push | ACL/no-store/digest, lockscreen/privacy, no live dispatch in dev, revoke/unregister, отдельное device E2E | зависит от C, policy/credentials |
| F. Release | полный main CI, independent security/QA, visual owner review, signed builds, upgrade/rollback, approved store/privacy/ops #119 | blocked |

Given A и B имеют разные verified links, When A передаёт UUID заказа B или после revoke пытается повторить старое решение, Then сервер не выдаёт объект и не изменяет состояние; native UI очищен. Given версия сметы изменилась, When отправлено старое решение, Then 409 и новое явное согласие; при потере HTTP-ответа exact retry возвращает тот же подтверждённый receipt только при сохранённых правах. Given приложение offline или вернулось из background, When нельзя проверить session/scope, Then приватные данные не отображаются и мутации недоступны.

Тесты будущей реализации: unit чистых DTO/state, contract OpenAPI, PG17 real auth/ACL/concurrency, emulator/simulator smoke на обоих OS, физические устройства для biometric/verified links/push/backup, ручная accessibility/visual и privacy review. Никакой фиктивной маркировки PASS по данному ADR.

## Обновления, откат и решения владельца

Release build запрещает synthetic endpoints/credentials, pinned toolchain, signing secrets только в защищённом CI; staged rollout, API сохраняет совместимость поддерживаемой версии, при incompatible version безопасное сообщение с обновлением. Не добавлять принудительный update, OTA или remote config, способные обойти signing/review. Disable native client/server feature gate при инциденте, revoke native families, web сохраняет доступ по прежним правилам. Rollback самого ADR — revert документации, без миграций.

Нужны утверждённые identity provider/redirect domains, OAuth token/session policy и revocation SLA, supported OS/устройства, offline/export/retention правила, consent и push providers, signing/store accounts, QA/security/legal/ops #119. Техническое предложение не заменяет решение владельца и независимые review. #117 и #118 остаются OPEN; никакого production deploy.

## Источники для проектирования

- [RFC 8252 — OAuth 2.0 for Native Apps](https://www.rfc-editor.org/rfc/rfc8252.html), [RFC 9700 — OAuth Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html).
- [Expo AuthSession](https://docs.expo.dev/versions/latest/sdk/auth-session/), [Expo SecureStore](https://docs.expo.dev/versions/latest/sdk/securestore/), [Apple Keychain](https://developer.apple.com/documentation/security/keychain-services), [Android Keystore](https://developer.android.com/privacy-and-security/keystore).
- [Issue #117](https://github.com/askarahodov/roboport/issues/117), [Issue #118](https://github.com/askarahodov/roboport/issues/118), [Issue #119](https://github.com/askarahodov/roboport/issues/119).

Источники проверены 26.11.2026; версии SDK и зависимости не выбраны. В документе только предложенная архитектура и критерии проверки, не утверждение о выполненной интеграции.
