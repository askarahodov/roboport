# Публичная заявка на запись с verified-handoff — current-dev (#442)

## Граница реальной системы

Включено **только в закрытом HTTPS loopback synthetic-dev**. Страница
`/book-dev` доступна без клиентской сессии; весь public API до релиза
используется только на localhost/127.0.0.1, без публичного домена.
Никаких реальных телефонов, VIN, ФИО, контактов, сообщений, реальной
CAPTCHA, юридического согласия по ПДн, банков/ККТ/уведомлений нет.
`processingConsent` и `marketingConsent` — тестовые неизменяемые
заявления для проверки маршрута, **не правовые основания маркетинга**.
`sendAllowed=false`; существующий `notification_outbox` остаётся
синтетическим sink без отправки.

## Полная проверяемая последовательность

1. MANAGER на `/staff/auto-public-booking` публикует конкретный
   филиал, пост, услугу из своего каталога и версию условий
   `DEV-CONSENT-2026`. Для предложения с материалами он должен
   одновременно иметь WAREHOUSE. Перечисленные SKU с разделением
   `PART` / `CONSUMABLE` и количеством записаны неизменяемо и
   **не видны гостю**.
2. Гость открывает `/book-dev`, указывает демо UUID организации и
   филиала, получает опубликованные только для них услуги. Сервер
   возвращает до 50 свободных кандидатов на локальный день в
   часовом поясе филиала, проверяя занятость поста и часы работы.
   Сигнал доступности материалов предварительный и не раскрывает
   закупочные цены, остатки либо номера деталей.
3. Перед POST заявки браузер получает одноразовый проверочный вопрос
   (`DEV_ARITHMETIC_ONLY`, срок пять минут). Это **НЕ настоящая
   CAPTCHA**, необходима провайдерная защита после #119.
   Глобальный лимит на организацию: не более 100 вопросов и 25
   заявок за скользящие 10 минут. POST принимает только same-origin
   application/json; Origin обязателен. Неподтверждённое
   `processingConsent=true` отклоняется 422; маркетинговый выбор
   необязательный и **по умолчанию false**.
4. POST создаёт **без PII/телефона/VIN/customer ID** immutable request,
   возвращает ID заявки и секретный receipt UUID. Потеря ответа:
   повторить **исходный POST с тем же requestKey и challengeId**,
   сервер отвечает 200 с тем же receipt. Слот **НЕ закреплён**:
   это заявка, а не запись/приглашение на конкретное время.
5. Клиент открывает `/account/auto-public-booking` и входит в
   собственный кабинет, вводит ID заявки + секретный ключ и UUID
   своей ранее verified-связи с автомобилем. Сервер по текущим
   CLIENT-role, session, original proof/acl, vehicle/branch связям
   и CSRF записывает один append-only claim. Чужой клиент, старое
   доказательство, чужой филиал или подмена UUID → 403. После
   claim можно отозвать необязательный маркетинговый выбор; withdrawal
   неизменяем, никакие сообщения в любом случае не отправляются.
6. INTAKE видит в `/staff/auto-public-booking` очередь без контактов
   и material requirements. После `WAITING_FOR_STAFF` сотрудник
   нажимает **«Создать запись, CRM-лид и резерв атомарно»**.
   Для требований склада обязательно одновременное WAREHOUSE grant.
   В **одной PostgreSQL транзакции** проверяются оригинальный
   proof, смена/доступность поста, часы, длительность услуг, наличие
   PART/CONSUMABLE; выполняются старые проверенные
   `create_appointment`, `auto_work_order`, `auto_work_order_service`,
   настоящий синтетический stock RESERVE + material selection,
   `auto_crm_lead` WEB/QUALIFIED/BOOKED (или привязка к
   существующему WEB-лиду) и append-only fulfillment.
   Если конкурент занял пост или закончился SKU, **вся** операция
   откатывается — нет частичного броня, резерва или CRM-дубля.
   PostgreSQL exclusion гарантирует отсутствие double-booking.
   Встроенный appointment outbox создаёт только synthetic event
   без отправки реальному клиенту.
7. На одном request допускается один verified claim и один
   fulfillment. Повтор с тем же ключом → 200 без повторного
   бронирования; повтор с другим ключом → 409. Ответ на
   `/status` по секретному receipt не содержит PII.

## Закрытый API, ошибки и ключи

`GET /api/v1/dev/auto/public-booking/offers?organizationId=…&locationId=…`,
`GET /api/v1/dev/auto/public-booking/slots?offerId=…&localDay=YYYY-MM-DD`.

Анонимные same-origin POST:
`/api/v1/dev/auto/public-booking/challenges`
(`offerId`); `/requests`
(`offerId,requestKey,challengeId,answer,startsAt,endsAt,processingConsent,marketingConsent`);
`/requests/{id}/status` (`receiptKey`).
Секреты отправляются **в теле POST, не query-string**.

CLIENT role/session + X-CSRF:
`/requests/{id}/claim`
(`receiptKey,claimKey,customerLinkId,processingConsent:true`),
`/requests/{id}/withdraw`
(`receiptKey,withdrawKey`).
MANAGER + X-CSRF:
`POST .../organizations/{org}/locations/{loc}/public-booking/offers`
(`bayId,serviceId,policyVersion,requestKey,materials[]`).
INTAKE:
`GET .../public-booking/queue` и
`POST .../public-booking/requests/{id}/fulfill`
(`requestKey`; плюс WAREHOUSE для материалов).
Все ответы `Cache-Control: no-store`. 201 новая запись,
200 точный повтор, 403 роль/ключ/proof/Origin, 409
конфликт состояния/слота/склада/дублирования,
422 неверное тело, 429 исчерпанный тестовый лимит.

## Данные, QA и блокеры

Alembic `0121_auto_public_booking`, новые таблицы immutable
offer/material/request/claim/withdrawal/fulfillment; одноразовый
challenge может только перейти в consumed. Нет контактного
политического или произвольного VIN поля. Роль филиала и
источник маршрута перепроверяются перед бронированием.

`tests/integration/test_pg_auto_public_booking_http.py`
проверяет фактический PostgreSQL 17+HTTPS happy flow, согласие,
ошибки/чужого клиента/отзыв proof, точный replay,
параллельные оформления одной заявки и гонку двух заявок
за один пост, материальный RESERVE, CRM стадии и
outbox. `apps/web/e2e/auto-public-booking.spec.ts` —
390/1280 и отказ/retry; остальные appointment/stock/CRM
тесты остаются частью полного 5/5 CI.

**Rollback:** нельзя удалять immutable consent/CRM/booking
историю в production. Alembic downgrade предназначен
исключительно для одноразового изолированного PG17.
Реальный провайдер CAPTCHA/anti-spam, публичный ingress
с реальными privacy terms/consent, контактная идентификация,
delivery callback/webhook, подтверждение и cancel/move
конкретной публичной заявки, продакшен-роль и двойное
ревью безопасности требуют отдельного #119 sign-off.
До этого **не открывать** маршрут в интернет и не закрывать
#442/родительские эпики как полностью production-ready.
