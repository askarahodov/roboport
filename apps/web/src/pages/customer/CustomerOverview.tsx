import React from 'react';
import { Link } from 'react-router-dom';

const sections = [
  { url: '/account/vehicles', label: 'Мои автомобили', description: 'Только подтверждённые связи с автомобилями' },
  { url: '/account/auto-booking', label: 'Запись на сервис', description: 'Доступные филиалы и время на тестовом стенде' },
  { url: '/account/auto-orders', label: 'Мои заказ-наряды', description: 'Только разрешённые сервером документы СТО' },
  { url: '/account/settings', label: 'Настройки и уведомления', description: 'Предпочтения связи для автомобильного сервиса' },
] as const;

export function CustomerOverview() {
  return <section aria-label="Обзор автомобильного кабинета">
    <h3>Автомобильный сервис</h3>
    <p>Синтетический стенд. Данные загружаются в выбранных разделах только после проверки серверной сессии, прав доступа и связи с автомобилем.</p>
    <ul className="account-stats">{sections.map(section=><li className="account-stat" key={section.url}>
      <Link className="account-stat-link" to={section.url}>{section.label} <span aria-hidden="true">↗</span></Link>
      <span>{section.description}</span>
    </li>)}</ul>
    <p>Реальные данные и юридически значимые операции недоступны до прохождения release-gate #119.</p>
  </section>;
}
