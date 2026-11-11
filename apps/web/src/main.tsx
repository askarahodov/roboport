import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Routes, Route } from 'react-router-dom';
import './style.css';
import { CustomerAccount } from './pages/customer/CustomerAccount';
import { CustomerLogin } from './pages/customer/CustomerLogin';
import { StaffPortal } from './pages/staff/StaffPortal';
import { AutoStaffAdmin } from './pages/staff/AutoStaffAdmin';
import { AutoCalendar } from './pages/staff/AutoCalendar';
import { AutoBayOccupancy } from './pages/staff/AutoBayOccupancy';
import { AutoDemoFinanceReport } from './pages/staff/AutoDemoFinanceReport';
import { AutoTreasury } from './pages/staff/AutoTreasury';
import { AutoPublicBooking } from './pages/shared/AutoPublicBooking';
import { AutoCrmLeads } from './pages/staff/AutoCrmLeads';
import { AutoCrmFunnel } from './pages/staff/AutoCrmFunnel';
import { AutoCrmOutcomes } from './pages/staff/AutoCrmOutcomes';
import { AutoStockAbcXyz } from './pages/staff/AutoStockAbcXyz';
import { AutoOrderServices } from './pages/staff/AutoOrderServices';
import { AutoWorkshopQc } from './pages/staff/AutoWorkshopQc';
import { AutoIntakeAct } from './pages/shared/AutoIntakeAct';
import { AutoDiagnostics } from './pages/staff/AutoDiagnostics';
import { AutoEstimateConsole } from './pages/shared/AutoEstimateConsole';
import { AutoWorkExecution } from './pages/staff/AutoWorkExecution';
import { AutoDemoClose } from './pages/shared/AutoDemoClose';
import { WarehouseStocktake } from './pages/staff/WarehouseStocktake';
import { WarehouseCatalog } from './pages/staff/WarehouseCatalog';
import { WarehouseReceiving } from './pages/staff/WarehouseReceiving';
import { WarehouseSupplierOrders } from './pages/staff/WarehouseSupplierOrders';
import { WarehouseWorkOrders } from './pages/staff/WarehouseWorkOrders';
import { WarehouseReclassification } from './pages/staff/WarehouseReclassification';
import { WarehouseSupplierReturns } from './pages/staff/WarehouseSupplierReturns';
import { WarehouseSerialMovements } from './pages/staff/WarehouseSerialMovements';
import { AutoUiGallery } from './pages/AutoUiGallery';

type Theme = 'light' | 'dark' | 'auto';

function App() {
  const [theme, setTheme] = React.useState<Theme>(() => {
    try {
      const stored = window.localStorage.getItem('roboport-theme');
      return stored === 'light' || stored === 'dark' || stored === 'auto' ? stored : 'auto';
    } catch {
      return 'auto';
    }
  });
  React.useEffect(() => {
    try { window.localStorage.setItem('roboport-theme', theme); } catch {
      // Сайт работает и при запрещённом browser storage; никаких секретов здесь нет.
    }
  }, [theme]);
  const [systemDark, setSystemDark] = React.useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  React.useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setSystemDark(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  const dark = theme === 'dark' || (theme === 'auto' && systemDark);
  return <main className={dark ? 'app dark' : 'app'}>
    <header>
      <Link to="/" aria-label="БоксРитм — главная"><picture><source media="(prefers-color-scheme: dark)" srcSet="/logo-dark.svg" /><img src="/logo-light.svg" alt="" width="64" height="64" /></picture><strong>БоксРитм</strong></Link>
      <nav aria-label="Разделы сервиса">
        <NavLink to="/" end>Главная</NavLink>
        <NavLink to="/account" end>Личный кабинет</NavLink>
        <NavLink to="/account/auto-booking" end>Запись на СТО (dev)</NavLink>
        <NavLink to="/book-dev" end>Публичная заявка (dev)</NavLink>
        <NavLink to="/account/auto-public-booking" end>Подтвердить заявку (dev)</NavLink>
        <NavLink to="/staff/auto-public-booking" end>Публикации СТО (dev)</NavLink>
        <NavLink to="/account/login" className="header-login">Войти</NavLink>
        <NavLink to="/staff" end>Сотрудникам</NavLink>
        <NavLink to="/staff/auto-admin" end>Организации (dev)</NavLink>
        <NavLink to="/staff/auto-calendar" end>Календарь (dev)</NavLink>
        <NavLink to="/staff/auto-bay-occupancy" end>Загрузка постов (dev)</NavLink>
        <NavLink to="/staff/auto-demo-finance-report" end>Тестовая финсверка (dev)</NavLink>
        <NavLink to="/staff/auto-treasury" end>Тестовая касса (dev)</NavLink>
        <NavLink to="/staff/auto-crm-leads" end>Обращения CRM (dev)</NavLink>
        <NavLink to="/staff/auto-crm-funnel" end>Воронка CRM (dev)</NavLink>
        <NavLink to="/staff/auto-crm-outcomes" end>CRM → закрытие (dev)</NavLink>
        <NavLink to="/staff/auto-stock-abc-xyz" end>ABC/XYZ склада (dev)</NavLink>
        <NavLink to="/staff/auto-order-services" end>Услуги наряда (dev)</NavLink>
        <NavLink to="/staff/auto-workshop-qc" end>Осмотр и QC (dev)</NavLink>
        <NavLink to="/staff/auto-intake-act" end>Акт приёмки (dev)</NavLink>
        <NavLink to="/staff/auto-diagnostics" end>Диагностика (dev)</NavLink>
        <NavLink to="/staff/auto-estimates" end>Сметы (dev)</NavLink>
        <NavLink to="/staff/auto-execution" end>Работы и материалы (dev)</NavLink>
        <NavLink to="/staff/auto-demo-close" end>Сверка и пакет (dev)</NavLink>
        <NavLink to="/staff/warehouse-stocktake" end>Инвентаризация (dev)</NavLink>
        <NavLink to="/staff/warehouse-catalog" end>Номенклатура (dev)</NavLink>
        <NavLink to="/staff/warehouse-receiving" end>Приход (dev)</NavLink>
        <NavLink to="/staff/warehouse-supplier-orders" end>Закупки (dev)</NavLink>
        <NavLink to="/staff/warehouse-work-orders" end>Выдача (dev)</NavLink>
        <NavLink to="/staff/warehouse-reclassification" end>Пересортица (dev)</NavLink>
        <NavLink to="/staff/warehouse-supplier-returns" end>Возврат поставщику (dev)</NavLink>
        <NavLink to="/staff/warehouse-serial-movements" end>Серии (dev)</NavLink>
      </nav>
      <label>Тема <select aria-label="Тема оформления" value={theme} onChange={event => setTheme(event.target.value as Theme)}><option value="auto">Авто</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label>
    </header>
    <Routes>
      <Route path="/dev/ui-gallery" element={<AutoUiGallery />} />
      <Route path="/" element={<div className="landing">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-content">
            <span className="eyebrow"><span className="status-dot" /> БоксРитм · платформа автосервиса</span>
            <h1 id="hero-title">Управляйте автосервисом <span>в едином ритме</span></h1>
            <p className="hero-lead">Запись на СТО, заказ-наряды, согласование работ и обслуживание автомобиля — в единой системе.</p>
            <div className="hero-actions">
              <Link className="btn btn-primary" to="/account/auto-booking">Запись на сервис (dev) <span aria-hidden="true">↗</span></Link>
              <Link className="btn btn-secondary" to="/account/login">Войти в кабинет</Link>
            </div>
            <p className="hero-note">Только синтетический стенд разработки. Реальные заказы, оплаты и выдача автомобилей до release-gate #119 недоступны.</p>
          </div>
          <div className="hero-visual" aria-hidden="true">
            <div className="orbital orbital-one" /><div className="orbital orbital-two" />
            <div className="hero-mark">Б<span>·</span></div>
            <div className="hero-visual-label">БОКСРИТМ / AUTO DIGITAL / DEV</div>
          </div>
        </section>
        <section className="landing-section" aria-labelledby="services-title">
          <div className="section-intro"><span className="eyebrow">АВТОМОБИЛЬНЫЙ СЕРВИС</span><h2 id="services-title">Путь автомобиля через СТО</h2>
            <p>Доступные в dev операции защищены серверной сессией и правами на объект.</p></div>
          <div className="service-grid">
            <article className="service-card"><span className="service-number">01 / ЗАПИСЬ</span><div className="service-icon">◇</div><h3>Запись на сервис</h3><p>Выбор доступного времени и филиала на тестовом стенде.</p></article>
            <article className="service-card"><span className="service-number">02 / ПРИЁМКА</span><div className="service-icon">⌘</div><h3>Осмотр автомобиля</h3><p>Состояние машины, диагностические работы и согласование сметы.</p></article>
            <article className="service-card"><span className="service-number">03 / РЕМОНТ</span><div className="service-icon">◎</div><h3>Заказ-наряд</h3><p>Работы, материалы, учёт исполнения и независимый контроль качества.</p></article>
            <article className="service-card"><span className="service-number">04 / ИСТОРИЯ</span><div className="service-icon">↗</div><h3>Кабинет владельца</h3><p>Подтверждённые автомобили и разрешённые заказ-наряды в dev-режиме.</p></article>
          </div>
        </section>
        <section className="landing-cta" aria-labelledby="discover-title">
          <div><span className="eyebrow">ЛИЧНЫЙ КАБИНЕТ</span><h2 id="discover-title">Ваши автомобили и сервисные обращения</h2>
            <p>Просмотр доступен только авторизованному клиенту. Сведения чужих владельцев не раскрываются.</p></div>
          <div className="hero-actions"><Link className="btn btn-secondary" to="/account/vehicles">Мои автомобили</Link><Link className="btn btn-primary" to="/account/auto-orders">Заказ-наряды</Link></div>
        </section>
        <footer className="site-footer"><strong>БоксРитм</strong><span>Автомобильная платформа · Синтетический контур разработки</span></footer>
      </div>} />
      <Route path="/account/login" element={<CustomerLogin />} />
      <Route path="/account/*" element={<CustomerAccount />} />
      <Route path="/staff/auto-admin" element={<AutoStaffAdmin />} />
      <Route path="/staff/auto-calendar" element={<AutoCalendar />} />
      <Route path="/staff/auto-bay-occupancy" element={<AutoBayOccupancy />} />
      <Route path="/staff/auto-demo-finance-report" element={<AutoDemoFinanceReport />} />
      <Route path="/staff/auto-treasury" element={<AutoTreasury />} />
      <Route path="/book-dev" element={<AutoPublicBooking mode="public" />} />
      <Route path="/account/auto-public-booking" element={<AutoPublicBooking mode="client" />} />
      <Route path="/staff/auto-public-booking" element={<AutoPublicBooking mode="staff" />} />
      <Route path="/staff/auto-crm-leads" element={<AutoCrmLeads />} />
      <Route path="/staff/auto-crm-funnel" element={<AutoCrmFunnel />} />
      <Route path="/staff/auto-crm-outcomes" element={<AutoCrmOutcomes />} />
      <Route path="/staff/auto-stock-abc-xyz" element={<AutoStockAbcXyz />} />
      <Route path="/staff/auto-order-services" element={<AutoOrderServices />} />
      <Route path="/staff/auto-workshop-qc" element={<AutoWorkshopQc />} />
      <Route path="/staff/auto-intake-act" element={<AutoIntakeAct role="staff" />} />
      <Route path="/staff/auto-diagnostics" element={<AutoDiagnostics />} />
      <Route path="/staff/auto-estimates" element={<AutoEstimateConsole role="manager" />} />
      <Route path="/staff/auto-execution" element={<AutoWorkExecution />} />
      <Route path="/staff/auto-demo-close" element={<AutoDemoClose role="staff" />} />
      <Route path="/account/auto-demo-close" element={<AutoDemoClose role="client" />} />
      <Route path="/account/auto-estimates" element={<AutoEstimateConsole role="client" />} />
      <Route path="/account/auto-intake-act" element={<AutoIntakeAct role="client" />} />
      <Route path="/staff/warehouse-catalog" element={<WarehouseCatalog />} />
      <Route path="/staff/warehouse-stocktake" element={<WarehouseStocktake />} />
      <Route path="/staff/warehouse-receiving" element={<WarehouseReceiving />} />
      <Route path="/staff/warehouse-supplier-orders" element={<WarehouseSupplierOrders />} />
      <Route path="/staff/warehouse-work-orders" element={<WarehouseWorkOrders />} />
      <Route path="/staff/warehouse-reclassification" element={<WarehouseReclassification />} />
      <Route path="/staff/warehouse-supplier-returns" element={<WarehouseSupplierReturns />} />
      <Route path="/staff/warehouse-serial-movements" element={<WarehouseSerialMovements />} />
      <Route path="/staff/*" element={<StaffPortal />} />
      <Route path="*" element={<section><h1>Страница не найдена</h1><p>Такого раздела пока нет.</p><Link to="/">Вернуться на главную</Link></section>} />
    </Routes>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>);
