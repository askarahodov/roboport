import * as React from 'react';
import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom';

type SessionState = 'loading' | 'authenticated' | 'anonymous' | 'unavailable';
type SectionId = 'overview' | 'robots' | 'tickets' | 'estimates' | 'documents' | 'settings';
const sections: Array<{ id: SectionId; label: string; description: string }> = [
  { id: 'overview', label: 'Обзор', description: 'Информация о ваших обращениях и обслуживании роботов.' },
  { id: 'robots', label: 'Роботы', description: 'Устройства, привязанные к вашему аккаунту.' },
  { id: 'tickets', label: 'Заявки', description: 'История ваших обращений в сервис.' },
  { id: 'estimates', label: 'Сметы', description: 'Сметы, доступные вам для ознакомления.' },
  { id: 'documents', label: 'Документы', description: 'Защищённые документы ваших заявок.' },
  { id: 'settings', label: 'Настройки и уведомления', description: 'Параметры вашего аккаунта.' },
];

type Ticket = { id: string; state: string; version: number };
function Tickets() {
  const [status, setStatus] = React.useState<'loading'|'ready'|'denied'|'unavailable'>('loading');
  const [items, setItems] = React.useState<Ticket[]>([]);
  const [retry, setRetry] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setStatus('loading');
      try {
        const response = await fetch('/api/v1/dev/tickets/keyset?limit=20', {
          credentials: 'include', cache: 'no-store', signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (controller.signal.aborted) return;
        if (response.status === 401 || response.status === 403) {
          setStatus('denied'); return;
        }
        if (!response.ok) { setStatus('unavailable'); return; }
        const json: unknown = await response.json();
        if (!json || typeof json !== 'object' || !('items' in json) ||
            !Array.isArray(json.items) || !json.items.every((item: unknown) =>
              item !== null && typeof item === 'object' &&
              'id' in item && typeof item.id === 'string' &&
              'state' in item && typeof item.state === 'string' &&
              'version' in item && typeof item.version === 'number')) {
          setStatus('unavailable'); return;
        }
        setItems(json.items as Ticket[]);
        setStatus('ready');
      } catch {
        if (!controller.signal.aborted) setStatus('unavailable');
      }
    }
    void load();
    return () => controller.abort();
  }, [retry]);
  return <section aria-labelledby="account-tickets">
    <h2 id="account-tickets">Заявки</h2>
    {status === 'loading' && <p role="status">Загрузка заявок…</p>}
    {status === 'denied' && <p role="alert">Сессия завершена или доступ отозван. Войдите повторно.</p>}
    {status === 'unavailable' && <><p role="alert">Не удалось получить заявки. Данные не показаны.</p>
      <button type="button" onClick={() => setRetry(value => value+1)}>Повторить загрузку</button></>}
    {status === 'ready' && (items.length === 0
      ? <p role="status">У вас пока нет доступных заявок.</p>
      : <ul>{items.map(item =>
          <li key={item.id}>Заявка {item.id} — {item.state}, версия {item.version}</li>)}</ul>)}
  </section>;
}

function Section({ id }: { id: SectionId }) {
  if (id === 'tickets') return <Tickets />;
  const section = sections.find(item => item.id === id)!;
  return <section aria-labelledby={'account-' + id}>
    <h2 id={'account-' + id}>{section.label}</h2>
    <p>{section.description}</p>
    <p role="status">Данные раздела пока недоступны. Приватная информация не загружается без серверной проверки доступа.</p>
    {id === 'documents' && <p>Файлы предоставляются только через защищённый сервер. Прямого доступа к MinIO нет.</p>}
  </section>;
}

/** Never accept a subject from storage, request headers or local mock data. */
export function Account() {
  const [state, setState] = React.useState<SessionState>('loading');
  const [refresh, setRefresh] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    async function inspect() {
      setState('loading');
      try {
        const response = await fetch('/api/v1/auth/me', {
          method: 'GET',
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (controller.signal.aborted) return;
        if (response.status === 401 || response.status === 403) {
          setState('anonymous');
          return;
        }
        if (!response.ok) {
          setState('unavailable');
          return;
        }
        const json: unknown = await response.json();
        if (json && typeof json === 'object' && 'subjectId' in json &&
            typeof json.subjectId === 'string' &&
            /^[0-9a-f-]{36}$/i.test(json.subjectId)) {
          setState('authenticated');
        } else {
          setState('unavailable');
        }
      } catch {
        if (!controller.signal.aborted) setState('unavailable');
      }
    }
    void inspect();
    return () => controller.abort();
  }, [refresh]);

  if (state === 'loading') return <section aria-live="polite"><h1>Личный кабинет</h1><p role="status">Проверка серверной сессии…</p></section>;
  if (state === 'anonymous') return <section><h1>Личный кабинет</h1>
    <p role="status">401 — требуется локальная авторизация. Вход пока не реализован.</p>
    <Link to="/">На главную</Link></section>;
  if (state === 'unavailable') return <section><h1>Личный кабинет</h1>
    <p role="alert">Не удалось проверить серверную сессию. Данные кабинета не показаны.</p>
    <button type="button" onClick={() => setRefresh(count => count + 1)}>Повторить проверку</button>
    </section>;
  return <div className="account-shell">
    <h1>Личный кабинет</h1>
    <p>Доступ подтверждён сервером. Разделы показывают данные только после отдельной проверки прав.</p>
    <nav className="account-nav" aria-label="Разделы личного кабинета">
      {sections.map(item => <NavLink key={item.id}
        to={'/account/' + item.id}>{item.label}</NavLink>)}
    </nav>
    <Routes>
      <Route index element={<Navigate to="overview" replace />} />
      {sections.map(item => <Route key={item.id} path={item.id} element={<Section id={item.id} />} />)}
      <Route path="*" element={<section><h2>Раздел не найден</h2><Link to="/account/overview">К обзору</Link></section>} />
    </Routes>
  </div>;
}
