import React from 'react';
import { PageShell, Tabs, Card } from '../../ui';
import { Link, useLocation } from 'react-router-dom';
import { CustomerTickets } from './CustomerTickets';
import { CustomerSummary } from './CustomerSummary';
import { CustomerOverview } from './CustomerOverview';
import { CustomerSettings } from './CustomerSettings';

type Session = { subjectId: string };
type State = 'loading' | 'authorized' | 'unauthorized' | 'forbidden' | 'error';
const sections = [
  { path: '/account', title: 'Обзор', end: true },
  { path: '/account/robots', title: 'Роботы' },
  { path: '/account/tickets', title: 'Заявки' },
  { path: '/account/estimates', title: 'Сметы' },
  { path: '/account/documents', title: 'Документы' },
  { path: '/account/settings', title: 'Настройки и уведомления' },
] as const;

export function CustomerAccount() {
  const { pathname } = useLocation();
  const [state, setState] = React.useState<State>('loading');
  const [attempt, setAttempt] = React.useState(0);
  const [session, setSession] = React.useState<Session | null>(null);
  const [logoutError,setLogoutError] = React.useState<string|null>(null);
  const [loggingOut,setLoggingOut] = React.useState(false);
  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(null);
    try {
      const csrfResponse=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if (!csrfResponse.ok) throw new Error('CSRF unavailable');
      const csrfData:unknown=await csrfResponse.json();
      if (!csrfData || typeof csrfData!=='object' ||
        !('csrfToken' in csrfData) || typeof csrfData.csrfToken!=='string') {
        throw new Error('Invalid CSRF');
      }
      const res=await fetch('/api/v1/auth/logout',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'X-CSRF-Token':csrfData.csrfToken},
      });
      if (!res.ok) throw new Error('Logout failed');
      setSession(null);
      setState('unauthorized');
    } catch {
      setLogoutError('Не удалось завершить сессию. Повторите попытку.');
    } finally { setLoggingOut(false); }
  }

  React.useEffect(() => {
    const controller = new AbortController();
    setState('loading');
    setSession(null);
    async function validate() {
      try {
        // Relative, same-origin only. Browser-managed HttpOnly session cookie;
        // never store access tokens, session IDs or CSRF credentials in storage.
        const res = await fetch('/api/v1/auth/me', {
          signal: controller.signal,
          credentials: 'include',
          headers: { Accept: 'application/json' },
          cache: 'no-store',
        });
        if (res.status === 401) { setState('unauthorized'); return; }
        if (res.status === 403 || res.status === 404) { setState('forbidden'); return; }
        if (!res.ok) throw new Error('Session unavailable');
        const data: unknown = await res.json();
        if (!data || typeof data !== 'object' || !('subjectId' in data)
          || typeof data.subjectId !== 'string') throw new Error('Invalid session');
        if (!controller.signal.aborted) {
          setSession({ subjectId: data.subjectId });
          setState('authorized');
        }
      } catch {
        if (!controller.signal.aborted) setState('error');
      }
    }
    void validate();
    return () => controller.abort();
  }, [attempt]);

  const selected = sections.find(section => section.path === pathname);
  if (!selected) return <section><h1>Раздел не найден</h1><Link to="/account">В кабинет</Link></section>;
  if (state === 'loading') return <section><h1>Личный кабинет</h1><p role="status">Проверка серверной сессии…</p></section>;
  if (state === 'unauthorized') return <section><h1>Личный кабинет</h1>
    <p role="status">401 — требуется локальная авторизация.</p>
    <Link to="/account/login">Войти</Link> <Link to="/">На главную</Link></section>;
  if (state === 'forbidden') return <section><h1>Доступ ограничен</h1>
    <p role="alert">403 — доступ запрещён или отозван. Обратитесь к администратору.</p>
    <div className="auth-recovery"><Link to="/account/login">Войти под другой учётной запись</Link><button type="button" onClick={() => setAttempt(n => n + 1)}>Повторить проверку</button></div>
    <p className="auth-help">Авторизация и разрешение на просмотр кабинета — разные проверки. Если доступ отозван, повторный вход не восстановит права автоматически.</p>
    <Link to="/">На главную</Link></section>;
  if (state === 'error') return <section><h1>Не удалось открыть кабинет</h1>
    <p role="alert">Не удалось проверить серверную сессию. Личные данные не загружены.</p>
    <button type="button" onClick={() => setAttempt(value => value + 1)}>Повторить проверку</button></section>;

  return <PageShell className="customer-account">
    <h1>Личный кабинет</h1>
    <div className="account-layout"><aside className="account-sidebar"><p className="ui-eyebrow">ЛИЧНОЕ ПРОСТРАНСТВО</p><Tabs label="Разделы личного кабинета" items={sections.map(section => ({to:section.path,label:section.title,end:'end' in section && section.end}))} /></aside><div className="account-content">
    <h2>{selected.title}</h2>
    <p className="account-subtitle">Здесь собраны ваши роботы, обращения, сметы и документы.</p>
    <Card>{pathname === '/account/tickets' ? <CustomerTickets /> :
      pathname === '/account/robots' ? <CustomerSummary section="robots" /> :
      pathname === '/account/estimates' ? <CustomerSummary section="estimates" /> :
      pathname === '/account/documents' ? <CustomerSummary section="documents" /> :
      pathname === '/account/settings' ? <CustomerSettings /> :
      <CustomerOverview />}</Card>
    <p className="account-hint">Настоящие документы доступны только через защищённый backend. Прямого подключения браузера к хранилищу нет.</p>
    <button type="button" onClick={() => setAttempt(value => value + 1)}>Проверить доступ снова</button>
    <button type="button" disabled={loggingOut} onClick={() => void logout()}>
      {loggingOut ? 'Выход…' : 'Выйти'}</button>
    {logoutError && <p role="alert">{logoutError}</p>}
    <span className="sr-only">Авторизованный субъект: {session?.subjectId}</span></div></div>
  </PageShell>;
}
