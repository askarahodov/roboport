import React from 'react';
import { Link } from 'react-router-dom';
import { StaffAutoClaims } from './StaffAutoClaims';
import { Alert, Skeleton, Badge, Button } from '../../ui';
import './staff.css';

type Role = 'INTAKE' | 'ENGINEER' | 'STOCK' | 'MANAGER' | 'FINANCE' | 'AUDITOR';

// Role-specific help reflects real dev scopes; docs links never carry credentials.
const staffGuides: { role: Role; title: string; path: string }[] = [
  {role:'INTAKE', title:'Мастер-приёмщик', path:'intake'},
  {role:'ENGINEER', title:'Механик и диагност', path:'mechanic'},
  {role:'STOCK', title:'Кладовщик и закупщик', path:'warehouse'},
  {role:'MANAGER', title:'Руководитель', path:'manager'},
  {role:'FINANCE', title:'Бухгалтер и кассир', path:'finance'},
  {role:'AUDITOR', title:'Проверка доступов и журналов', path:'ACCESS_AND_TEST_MATRIX'},
];
const sectionGuide: Record<string,string> = {
  '/staff/auto-admin':'administrator',
  '/staff/auto-calendar':'intake', '/staff/auto-bay-occupancy':'manager',
  '/staff/auto-crm-leads':'intake', '/staff/auto-crm-funnel':'manager',
  '/staff/auto-crm-outcomes':'manager',
  '/staff/auto-mechanic-productivity':'manager',
  '/staff/auto-intake-act':'intake', '/staff/auto-diagnostics':'mechanic',
  '/staff/auto-estimates':'manager', '/staff/auto-execution':'mechanic',
  '/staff/auto-workshop-qc':'mechanic', '/staff/auto-demo-close':'manager',
  '/staff/auto-demo-finance-report':'finance', '/staff/auto-treasury':'finance', '/staff/auto-order-services':'intake',
  '/staff/auto-stock-abc-xyz':'warehouse',
  '/staff/warehouse-catalog':'warehouse',
  '/staff/warehouse-stocktake':'warehouse', '/staff/warehouse-receiving':'warehouse',
  '/staff/warehouse-supplier-orders':'warehouse',
  '/staff/warehouse-work-orders':'warehouse', '/staff/warehouse-reclassification':'warehouse',
  '/staff/warehouse-supplier-returns':'warehouse', '/staff/warehouse-serial-movements':'warehouse',
};
const helpUrl = (path: string) => '/docs/#/guides/' + path;
const sections: { to: string; label: string; roles: Role[] }[] = [
  { to: '/staff/auto-admin', label: 'Организации и филиалы (dev)', roles: ['MANAGER'] },
  { to: '/staff/auto-calendar', label: 'Календарь записи (dev)', roles: ['INTAKE','MANAGER'] },
  { to: '/staff/auto-bay-occupancy', label: 'Загрузка постов (dev)', roles: ['INTAKE','MANAGER'] },
  { to: '/staff/auto-crm-leads', label: 'Обращения CRM (dev)', roles: ['INTAKE','MANAGER'] },
  { to: '/staff/auto-crm-funnel', label: 'Воронка CRM (dev)', roles: ['MANAGER'] },
  { to: '/staff/auto-crm-outcomes', label: 'CRM → техзакрытие (dev)', roles: ['MANAGER'] },
  { to: '/staff/auto-mechanic-productivity', label: 'Выработка механиков (dev)', roles: ['MANAGER'] },
  { to: '/staff/auto-intake-act', label: 'Акт приёмки (dev)', roles: ['INTAKE','MANAGER'] },
  { to: '/staff/auto-diagnostics', label: 'Диагностика автомобиля (dev)', roles: ['ENGINEER','MANAGER'] },
  { to: '/staff/auto-estimates', label: 'Сметы СТО (dev)', roles: ['INTAKE','MANAGER'] },
  { to: '/staff/auto-execution', label: 'Работы и материалы (dev)', roles: ['ENGINEER','MANAGER'] },
  { to: '/staff/auto-workshop-qc', label: 'Контроль качества (dev)', roles: ['ENGINEER','MANAGER'] },
  { to: '/staff/auto-demo-close', label: 'Тестовая сверка (dev)', roles: ['MANAGER','FINANCE'] },
  { to: '/staff/auto-demo-finance-report', label: 'Финансовый отчёт (dev)', roles: ['MANAGER','FINANCE'] },
  { to: '/staff/auto-treasury', label: 'Кассы, авансы и возвраты (dev)', roles: ['MANAGER','FINANCE'] },
  { to: '/staff/auto-order-services', label: 'Услуги заказ-нарядов (dev)', roles: ['INTAKE','MANAGER'] },
  { to: '/staff/auto-stock-abc-xyz', label: 'ABC/XYZ склада (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-catalog', label: 'Номенклатура (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-stocktake', label: 'Инвентаризация (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-receiving', label: 'Приход запчастей (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-supplier-orders', label: 'Заказы поставщикам (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-work-orders', label: 'Выдача на заказ-наряд (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-reclassification', label: 'Пересортица (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-supplier-returns', label: 'Возврат поставщику (dev)', roles: ['STOCK','MANAGER'] },
  { to: '/staff/warehouse-serial-movements', label: 'Серийные запчасти (dev)', roles: ['STOCK','MANAGER'] },
];

export function StaffPortal() {
  const [state, setState] = React.useState<'loading'|'ready'|'login'|'forbidden'|'error'>('loading');
  const [roles, setRoles] = React.useState<Role[]>([]);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setState('loading');setRoles([]);
    (async () => {
      try {
        const res = await fetch('/api/v1/dev/staff/access', {
          credentials: 'include', cache: 'no-store', signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (res.status === 401) { setState('login'); return; }
        if (res.status === 403 || res.status === 404) { setState('forbidden'); return; }
        if (!res.ok) throw new Error('access unavailable');
        const body: unknown = await res.json();
        const permitted: Role[] = ['INTAKE','ENGINEER','STOCK','MANAGER','FINANCE','AUDITOR'];
        if (!body || typeof body !== 'object' || !('roles' in body) ||
            !Array.isArray(body.roles) ||
            !body.roles.every((role: unknown) => typeof role === 'string' && permitted.includes(role as Role))) {
          throw new Error('invalid access response');
        }
        if (!controller.signal.aborted) { setRoles(body.roles as Role[]); setState('ready'); }
      } catch {
        if (!controller.signal.aborted) setState('error');
      }
    })();
    return () => controller.abort();
  }, [attempt]);

  const allowed = sections.filter(section => section.roles.some(role => roles.includes(role)));
  return <section className="ui-page crm-workspace">
    <div className="crm-heading"><div><span className="ui-eyebrow">БОКСРИТМ / АВТОМОБИЛЬНЫЙ СЕРВИС</span>
      <h1>Рабочее место СТО</h1>
      <p>Автомобили, заказ-наряды, диагностика, склад и CRM. Только синтетический стенд.</p>
    </div><Badge tone="warning">DEV / TEST</Badge></div>
    <nav aria-label="Справка сотрудника" className="staff-help-links">
      <a href={helpUrl('QUICK_START')}>Быстрый старт</a>
      <a href={helpUrl('ROLE_NAVIGATOR')}>Навигатор операций</a>
      <a href={helpUrl('FAQ')}>Ошибки 401/403/409 и помощь</a>
    </nav>
    {state === 'loading' && <Skeleton label="Проверка прав сотрудника…" />}
    {state === 'login' && <Alert>401 — требуется локальный вход. <Link to="/account/login">Перейти ко входу</Link></Alert>}
    {state === 'forbidden' && <Alert>403 — раздел недоступен: нет подтверждённых прав.</Alert>}
    {state === 'error' && <div><Alert>Не удалось проверить права на сервере.</Alert>
      <Button onClick={() => setAttempt(a => a + 1)}>Повторить</Button></div>}
    {state === 'ready' && <div className="crm-layout">
      <aside className="crm-rail" aria-label="Разделы СТО">
        <div className="crm-rail-brand">БоксРитм<small>КАБИНЕТ СОТРУДНИКА</small></div>
        <h2>Доступные разделы</h2>
        <nav aria-label="Навигация СТО" className="crm-menu">
          {allowed.map(section => <Link key={section.to} to={section.to}>{section.label}</Link>)}
        </nav>
        <div className="crm-help" aria-label="Инструкции доступных разделов">
          <h2>Помощь по операциям</h2>
          {allowed.map(section=><a key={section.to} href={helpUrl(sectionGuide[section.to])}>
            Инструкция: {section.label}</a>)}
        </div>
        <p className="crm-security">Каждая операция дополнительно проверяет tenant, роль, филиал и объект на сервере.</p>
      </aside>
      <div className="crm-main">
        <h2>Автомобильные операции</h2>
        <p>Роли: {roles.join(' · ') || 'нет назначенных ролей'}. Интерфейс не заменяет серверные проверки прав.</p>
        <nav aria-label="Руководства моих ролей" className="staff-role-guides">
          {staffGuides.filter(guide => roles.includes(guide.role)).map(guide=>
            <a key={guide.role} href={helpUrl(guide.path)}>{guide.title}</a>)}
          {roles.includes('MANAGER') && <a href={helpUrl('administrator')}>Организации и назначения</a>}
        </nav>
        {allowed.length === 0 && <Alert>Для этой роли пока нет доступных автомобильных разделов.</Alert>}
        {roles.some(r => r === 'INTAKE' || r === 'MANAGER') && <StaffAutoClaims roles={roles} />}
      </div>
    </div>}
  </section>;
}
