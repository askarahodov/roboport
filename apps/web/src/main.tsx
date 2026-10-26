import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Routes, Route } from 'react-router-dom';
import type { paths } from './shared/api/schema';
import './style.css';
import { CustomerAccount } from './pages/customer/CustomerAccount';
import { CustomerLogin } from './pages/customer/CustomerLogin';
import { StaffPortal } from './pages/staff/StaffPortal';
import { PublicCatalog, PublicModelDetails } from './pages/public/PublicCatalog';

type Brands = paths['/api/v1/catalog/brands']['get']['responses']['200']['content']['application/json'];
type Models = paths['/api/v1/catalog/models']['get']['responses']['200']['content']['application/json'];
type CatalogItem = { id: string; name: string };
type Theme = 'light' | 'dark' | 'auto';

function Catalog({ kind }: { kind: 'brands' | 'models' }) {
  const [items, setItems] = React.useState<CatalogItem[]>([]);
  const [attempt, setAttempt] = React.useState(0);
  const [status, setStatus] = React.useState<'loading' | 'success' | 'error'>('loading');
  React.useEffect(() => {
    const controller = new AbortController();
    const endpoint = kind === 'brands' ? '/api/v1/catalog/brands' : '/api/v1/catalog/models';
    async function load() {
      setStatus('loading');
      try {
        const response = await fetch(endpoint, { signal: controller.signal, credentials: 'omit' });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const data: Brands | Models = await response.json();
        if (!data || !Array.isArray(data.items)) throw new Error('Некорректный ответ каталога');
        setItems(data.items);
        setStatus('success');
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    }
    void load();
    return () => controller.abort();
  }, [kind, attempt]);
  const title = kind === 'brands' ? 'Бренды роботов' : 'Модели роботов';
  return <section aria-live="polite">
    <h1>{title}</h1>
    <p>Демонстрационный каталог. Реальные обращения пока не принимаются.</p>
    {status === 'loading' && <p role="status">Загрузка каталога…</p>}
    {status === 'error' && <div><p role="alert">Каталог сейчас недоступен. Попробуйте повторить запрос.</p><button type="button" onClick={() => setAttempt(n => n + 1)}>Повторить запрос</button></div>}
    {status === 'success' && (items.length
      ? <ul>{items.map(item => <li key={item.id}>{item.name}</li>)}</ul>
      : <p>Пока нет опубликованных {kind === 'brands' ? 'брендов' : 'моделей'}.</p>)}
  </section>;
}

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
      <Link to="/" aria-label="ROBOport — главная">
        <picture><source media="(prefers-color-scheme: dark)" srcSet="/logo-dark.svg" />
          <img src="/logo-light.svg" alt="" width="64" height="64" /></picture>
        <strong>ROBOport</strong>
      </Link>
      <nav aria-label="Разделы сервиса">
        <NavLink to="/" end>Главная</NavLink>
        <NavLink to="/catalog/brands" end>Бренды</NavLink>
        <NavLink to="/catalog/models" end>Модели</NavLink>
        <NavLink to="/account" end>Личный кабинет</NavLink>
        <NavLink to="/account/login" className="header-login">Войти</NavLink>
        <NavLink to="/staff" end>Сотрудникам</NavLink>
      </nav>
      <label>Тема <select aria-label="Тема оформления" value={theme} onChange={event => setTheme(event.target.value as Theme)}><option value="auto">Авто</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label>
    </header>
    <Routes>
      <Route path="/" element={<div className="landing">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-content">
            <span className="eyebrow"><span className="status-dot" /> ROBOport · независимый сервис робототехники</span>
            <h1 id="hero-title">Сервис обслуживания <span>роботов</span></h1>
            <p className="hero-lead">Диагностика, ремонт, обслуживание и модернизация домашних и человекоподобных роботов — в едином сервисном пространстве.</p>
            <div className="hero-actions">
              <Link className="btn btn-primary" to="/catalog/models">Смотреть модели <span aria-hidden="true">↗</span></Link>
              <Link className="btn btn-secondary" to="/account/login">Войти в кабинет</Link>
            </div>
            <p className="hero-note">Синтетический стенд разработки. Приём реальных заявок пока закрыт.</p>
          </div>
          <div className="hero-visual" aria-hidden="true">
            <div className="orbital orbital-one" /><div className="orbital orbital-two" />
            <div className="hero-mark">R<span>·</span></div>
            <div className="hero-visual-label">ROBOTICS / SERVICE / FUTURE</div>
          </div>
        </section>
        <section className="landing-section" aria-labelledby="services-title">
          <div className="section-intro"><span className="eyebrow">НАПРАВЛЕНИЯ СЕРВИСА</span><h2 id="services-title">Полный цикл заботы о роботе</h2>
          <p>От первой проверки до сопровождения после обслуживания.</p></div>
          <div className="service-grid">
            <article className="service-card"><span className="service-number">01 / АНАЛИЗ</span><div className="service-icon">◇</div><h3>Диагностика</h3><p>Структурированный поиск неисправностей и оценка технического состояния.</p></article>
            <article className="service-card"><span className="service-number">02 / ВОССТАНОВЛЕНИЕ</span><div className="service-icon">⌘</div><h3>Ремонт</h3><p>Сервисные работы с контролем этапов и согласованием сметы.</p></article>
            <article className="service-card"><span className="service-number">03 / ПОДДЕРЖКА</span><div className="service-icon">◎</div><h3>Обслуживание</h3><p>Профилактика, проверка узлов и ведение истории сервисных работ.</p></article>
            <article className="service-card"><span className="service-number">04 / РАЗВИТИЕ</span><div className="service-icon">↗</div><h3>Модернизация</h3><p>Оценка совместимости комплектующих и возможностей обновления.</p></article>
          </div>
        </section>
        <section className="landing-cta" aria-labelledby="discover-title">
          <div><span className="eyebrow">ИССЛЕДУЙТЕ КАТАЛОГ</span><h2 id="discover-title">Роботы разные. Сервис — системный.</h2>
          <p>Познакомьтесь с доступными разделами каталога. Данные выводятся из API.</p></div>
          <div className="hero-actions"><Link className="btn btn-secondary" to="/catalog/brands">Бренды</Link><Link className="btn btn-primary" to="/catalog/models">Модели роботов</Link></div>
        </section>
        <footer className="site-footer"><strong>ROBOport</strong><span>Сервисная платформа робототехники · Демонстрационный контур</span></footer>
      </div>} />
      <Route path="/robots" element={<PublicCatalog kind="models" />} />
      <Route path="/catalog/brands" element={<PublicCatalog kind="brands" />} />
      <Route path="/catalog/models" element={<PublicCatalog kind="models" />} />
      <Route path="/catalog/models/:modelId" element={<PublicModelDetails />} />
      <Route path="/account/login" element={<CustomerLogin />} />
      <Route path="/account/*" element={<CustomerAccount />} />
      <Route path="/staff/*" element={<StaffPortal />} />
      <Route path="*" element={<section><h1>Страница не найдена</h1><p>Такого раздела пока нет.</p><Link to="/">Вернуться на главную</Link></section>} />
    </Routes>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>);
