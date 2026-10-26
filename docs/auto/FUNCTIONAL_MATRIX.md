# Матрица возможностей AutoDealer → Airmatik

Сравнение публично заявленных возможностей AutoDealer и существующих модулей ROBOport. Исходники: https://autodealer.ru/online/help/o-programme.html ; https://autodealer.ru/online/order ; https://autodealer.ru/catalog .

| Модуль | Публичный AutoDealer | ROBOport сегодня | Адаптация | Этап |
|---|---|---|---|---|
| Клиенты и история | Есть | customer/ticket/ACL | auto customer/vehicle/order | MVP |
| VIN/автомобиль | Есть | robot_device | auto_vehicle, нормализация VIN | MVP |
| Онлайн-запись и посты | Есть | Нет auto scheduling | appointment + collision guard | MVP |
| Заказ-наряд | Есть | robot ticket/FSM | авто работы, пробег, осмотр | MVP |
| Смета и согласие | Есть | estimate/immutable | сохранить инварианты | MVP |
| Фото и документы | Есть | MinIO/ACL | автомобильный заказ | MVP |
| Склад/резервирование | Есть | inventory | auto parts and bin | MVP |
| OEM/аналоги | Есть | каталог роботов | licensed provider | MVP |
| CRM сотрудников | Есть | staff workspace | автомобильные сценарии | MVP |
| Кабинет клиента | Есть | robot account | авто, запись, документы | MVP |
| Поставщики/проценка | Есть | нет полноты | supplier offers | 2 |
| Касса/возвраты | Есть | нет | fiscal/payments | 2 |
| Звонки/мессенджеры | Есть | outbox | connectors | 2 |
| Аналитика/зарплаты | Есть | нет | reports | 2 |
| Мобильный клиент | Есть | responsive web | iOS/Android shared API | 3 |

Наличие исходного ticket или stock_lot не означает готовый автомобильный функционал. Условия использования справочников и охват базы 2026 требуют договора и проверки.
