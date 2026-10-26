# Автокаталоги 2026 — предварительный выбор

Не воспроизводить защищённые базы конкурента. Разделить OEM/aftermarket справочник, склад, торговые предложения. Интеграция только через лицензионный API.

| Провайдер | Заявленные возможности | Что проверить |
|---|---|---|
| Laximo | OEM/aftermarket, API, применяемость | договор, охват, кеширование |
| PartsAPI | марки/модели, VIN, кроссы, TecDoc, нормочасы | версия Q2 2026, регион, API тариф |
| ABCP | каталоги товарных групп, кроссы и API | отдельная подписка, лицензия, изображения |
| AutoDealer Catalog | оригинальные схемы, VIN/Frame, API как опция | договорное API, права данных |

Публичные источники: https://doc.laximo.ru/ ; https://partsapi.ru/docs ; https://www.abcp.ru/catalogs ; https://autodealer.ru/catalog .

Контракт PartsCatalogProvider: findVehicle, searchByOem, listAlternatives, getApplicability, getAssemblies. Результат: provider, sourceVersion, retrievedAt, brand, article, applicability (VERIFIED/UNVERIFIED/UNKNOWN). Ошибки: LICENSE_REQUIRED, RATE_LIMITED, UNAVAILABLE, NOT_FOUND, INVALID_INPUT. SupplierOffer имеет свою цену, остаток и ETA и не является доказательством применимости.

Реальные ключи и актуальность версии на октябрь 2026 не подтверждены. До договора используется только fixture adapter. Track #111.
