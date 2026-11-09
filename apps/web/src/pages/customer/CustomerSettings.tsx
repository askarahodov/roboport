import React from 'react';
import { CustomerCrmContactPreferences } from './CustomerCrmContactPreferences';

/**
 * В автомобильном кабинете есть только предпочтения связи CRM.
 * Исторический флаг robot-уведомлений больше не читается и не изменяется.
 * Сообщения наружу запрещены до независимого production-gate.
 */
export function CustomerSettings() {
  return <section aria-label="Настройки автомобильного кабинета">
    <h2>Настройки связи</h2>
    <p>Выбор каналов связи действует только на синтетическом стенде. Реальная отправка уведомлений отключена.</p>
    <CustomerCrmContactPreferences />
  </section>;
}
