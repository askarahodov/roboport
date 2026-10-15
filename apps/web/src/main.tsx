import React from 'react';
import { createRoot } from 'react-dom/client';
import type { paths } from './shared/api/schema';
import './style.css';

type Brands = paths['/api/v1/catalog/brands']['get']['responses']['200']['content']['application/json'];
type Models = paths['/api/v1/catalog/models']['get']['responses']['200']['content']['application/json'];
type CatalogItem = { id: string; name: string };
type View = 'home' | 'brands' | 'models' | 'account' | 'staff' | 'not-found';
type Theme = 'light' | 'dark' | 'auto';

function route(): View {
  if (window.location.pathname === '/catalog/brands') return 'brands';
  if (window.location.pathname === '/catalog/models') return 'models';
  if (window.location.pathname === '/account') return 'account';
  if (window.location.pathname === '/staff') return 'staff';
  if (window.location.pathname === '/') return 'home';
  return 'not-found';
}

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
  const [theme, setTheme] = React.useState<Theme>('auto');
  const [systemDark, setSystemDark] = React.useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  React.useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setSystemDark(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  const dark = theme === 'dark' || (theme === 'auto' && systemDark);
  const view = route();
  return <main className={dark ? 'app dark' : 'app'}>
    <header>
      <a href="/" aria-label="ROBOport — главная">
        <picture><source media="(prefers-color-scheme: dark)" srcSet="/logo-dark.svg" />
          <img src="/logo-light.svg" alt="" width="64" height="64" /></picture>
        <strong>ROBOport</strong>
      </a>
      <nav aria-label="Разделы сервиса">
        <a href="/" aria-current={view === 'home' ? 'page' : undefined}>Главная</a>
        <a href="/catalog/brands" aria-current={view === 'brands' ? 'page' : undefined}>Бренды</a>
        <a href="/catalog/models" aria-current={view === 'models' ? 'page' : undefined}>Модели</a>
        <a href="/account" aria-current={view === 'account' ? 'page' : undefined}>Личный кабинет</a>
        <a href="/staff" aria-current={view === 'staff' ? 'page' : undefined}>Сотрудникам</a>
      </nav>
      <label>Тема <select aria-label="Тема оформления" value={theme} onChange={event => setTheme(event.target.value as Theme)}><option value="auto">Авто</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label>
    </header>
    {view === 'account' || view === 'staff'
      ? <section><h1>{view === 'account' ? 'Личный кабинет' : 'Портал сотрудников'}</h1><p role="status">401 — требуется локальная авторизация. Вход пока не реализован.</p><a href="/">На главную</a></section>
      : view === 'not-found'
      ? <section><h1>Страница не найдена</h1><p>Такого раздела пока нет.</p><a href="/">Вернуться на главную</a></section>
      : view === 'home'
      ? <section><h1>Сервис обслуживания роботов</h1>
          <p>Синтетический стенд разработки. Приём реальных заявок пока закрыт.</p>
          <p>Изучите <a href="/catalog/brands">бренды</a> и <a href="/catalog/models">модели</a> роботов.</p>
        </section>
      : <Catalog kind={view} />}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
