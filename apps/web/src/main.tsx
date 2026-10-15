import React from 'react';
import { createRoot } from 'react-dom/client';
import type { paths } from './shared/api/schema';
import './style.css';

type Brands = paths['/api/v1/catalog/brands']['get']['responses']['200']['content']['application/json'];
type Models = paths['/api/v1/catalog/models']['get']['responses']['200']['content']['application/json'];
type CatalogItem = { id: string; name: string };
type View = 'home' | 'brands' | 'models';

function route(): View {
  if (window.location.pathname === '/catalog/brands') return 'brands';
  if (window.location.pathname === '/catalog/models') return 'models';
  return 'home';
}

function Catalog({ kind }: { kind: 'brands' | 'models' }) {
  const [items, setItems] = React.useState<CatalogItem[]>([]);
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
  }, [kind]);
  const title = kind === 'brands' ? 'Бренды роботов' : 'Модели роботов';
  return <section aria-live="polite">
    <h1>{title}</h1>
    <p>Демонстрационный каталог. Реальные обращения пока не принимаются.</p>
    {status === 'loading' && <p role="status">Загрузка каталога…</p>}
    {status === 'error' && <p role="alert">Каталог сейчас недоступен. Попробуйте обновить страницу.</p>}
    {status === 'success' && (items.length
      ? <ul>{items.map(item => <li key={item.id}>{item.name}</li>)}</ul>
      : <p>Пока нет опубликованных {kind === 'brands' ? 'брендов' : 'моделей'}.</p>)}
  </section>;
}

function App() {
  const [dark, setDark] = React.useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  const view = route();
  return <main className={dark ? 'app dark' : 'app'}>
    <header>
      <a href="/" aria-label="ROBOport — главная">
        <picture><source media="(prefers-color-scheme: dark)" srcSet="/logo-dark.svg" />
          <img src="/logo-light.svg" alt="" width="64" height="64" /></picture>
        <strong>ROBOport</strong>
      </a>
      <nav aria-label="Разделы сервиса">
        <a href="/">Главная</a>
        <a href="/catalog/brands">Бренды</a>
        <a href="/catalog/models">Модели</a>
      </nav>
      <button onClick={() => setDark(!dark)} aria-label="Переключить тему">Тема</button>
    </header>
    {view === 'home'
      ? <section><h1>Сервис обслуживания роботов</h1>
          <p>Синтетический стенд разработки. Приём реальных заявок пока закрыт.</p>
          <p>Изучите <a href="/catalog/brands">бренды</a> и <a href="/catalog/models">модели</a> роботов.</p>
        </section>
      : <Catalog kind={view} />}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
