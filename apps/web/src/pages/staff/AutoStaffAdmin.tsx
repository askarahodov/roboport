import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Grant = { grantId: string; userId: string; role: string; locationId: string | null };
const roles = ['MANAGER', 'INTAKE', 'MECHANIC', 'WAREHOUSE'] as const;

async function mutate(url: string, payload: unknown) {
  const csrf = await fetch('/api/v1/auth/csrf', { method: 'POST', credentials: 'include', cache: 'no-store' });
  if (!csrf.ok) throw new Error('Сессия недоступна или истекла');
  const data: unknown = await csrf.json();
  if (!data || typeof data !== 'object' || !('csrfToken' in data) ||
      typeof data.csrfToken !== 'string') throw new Error('Не удалось проверить CSRF');
  return fetch(url, { method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': data.csrfToken },
    body: JSON.stringify(payload) });
}

export function AutoStaffAdmin() {
  const [organization, setOrganization] = React.useState('');
  const [user, setUser] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [role, setRole] = React.useState<typeof roles[number]>('INTAKE');
  const [items, setItems] = React.useState<Grant[]>([]);
  const [state, setState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [busy, setBusy] = React.useState(false);
  const [confirmId, setConfirmId] = React.useState<string | null>(null);
  const [message, setMessage] = React.useState('');
  const [refresh, setRefresh] = React.useState(0);
  const validOrg = uuid.test(organization.trim());
  React.useEffect(() => { setConfirmId(null); }, [organization]);

  React.useEffect(() => {
    if (!validOrg) { setItems([]); setState('idle'); return; }
    const controller = new AbortController();
    setItems([]); setState('loading');
    void (async () => {
      try {
        const response = await fetch('/api/v1/dev/auto/organizations/' + encodeURIComponent(organization.trim()) + '/staff',
          { credentials: 'include', cache: 'no-store', signal: controller.signal });
        if (controller.signal.aborted) return;
        if ([401, 403, 404].includes(response.status)) { setState('denied'); return; }
        if (!response.ok) throw new Error();
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('items' in data) || !Array.isArray(data.items)
          || !data.items.every((item: unknown) => item && typeof item === 'object'
            && 'grantId' in item && typeof item.grantId === 'string' && uuid.test(item.grantId)
            && 'userId' in item && typeof item.userId === 'string' && uuid.test(item.userId)
            && 'role' in item && typeof item.role === 'string' && roles.some(r => r === item.role)
            && 'locationId' in item && (item.locationId === null || typeof item.locationId === 'string' && uuid.test(item.locationId)))) throw new Error();
        if (!controller.signal.aborted) { setItems(data.items as Grant[]); setState('ready'); }
      } catch { if (!controller.signal.aborted) setState('error'); }
    })();
    return () => controller.abort();
  }, [organization, refresh, validOrg]);

  async function execute(path: string, payload: unknown, action: 'grant'|'revoke') {
    if (busy || !validOrg) return;
    setBusy(true); setMessage('');
    try {
      const res = await mutate(path, payload);
      if (!res.ok) {
        setMessage(res.status === 409 ? '409 — такое активное назначение уже существует.'
          : [401,403,404].includes(res.status) ? '403 — операция не разрешена сервером.'
          : 'Операция не выполнена (' + res.status + ').');
        return;
      }
      setMessage(action === 'grant' ? 'Назначение создано.' : 'Доступ сотрудника отозван.');
      setRefresh(n => n + 1);
    } catch { setMessage('Ошибка соединения. Проверьте локальный стенд и сессию.'); }
    finally { setBusy(false); }
  }

  const base = '/api/v1/dev/auto/organizations/' + encodeURIComponent(organization.trim()) + '/staff';
  return <section aria-label="Сотрудники организации (dev)" style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Сотрудники организации</h1>
    <p>Только локальный синтетический стенд. Права проверяет сервер, а не этот экран. Реальных сотрудников не добавляйте.</p>
    <label>UUID организации <input value={organization} onChange={e => setOrganization(e.target.value)}
      placeholder="UUID организации" aria-label="UUID организации" /></label>
    {state === 'idle' && <p>Укажите корректный UUID организации.</p>}
    {state === 'loading' && <p role="status">Загрузка назначений…</p>}
    {state === 'denied' && <p role="alert">Доступ запрещён или dev API отключён.</p>}
    {state === 'error' && <p role="alert">Не удалось получить назначения.
      <button type="button" onClick={() => setRefresh(n => n + 1)}>Повторить</button></p>}
    {state === 'ready' && <><h2>Активные назначения</h2>
      {items.length ? <ul>{items.map(g => <li key={g.grantId}>
        <strong>{g.role}</strong> · {g.userId} · {g.locationId ?? 'Все филиалы'}
        {confirmId === g.grantId ? <span>
          <button type="button" disabled={busy} onClick={() => {
            setConfirmId(null);
            void execute(base + '/' + encodeURIComponent(g.grantId) + '/revoke', {}, 'revoke');
          }}>Подтвердить отзыв</button>
          <button type="button" disabled={busy} onClick={() => setConfirmId(null)}>Отмена</button>
        </span> : <button type="button" disabled={busy} onClick={() => setConfirmId(g.grantId)}>
          Отозвать
        </button>}</li>)}</ul> : <p>Активных назначений нет.</p>}
      <form onSubmit={e => {
        e.preventDefault();
        if (!uuid.test(user.trim()) || location.trim() && !uuid.test(location.trim())) return;
        void execute(base, { userId: user.trim(), role, locationId: location.trim() || null }, 'grant');
      }}>
        <h2>Назначить сотрудника</h2>
        <label>UUID пользователя <input required value={user} onChange={e => setUser(e.target.value)} /></label>
        <label>Роль <select value={role} onChange={e => setRole(e.target.value as typeof roles[number])}>
          {roles.map(r => <option key={r} value={r}>{r}</option>)}</select></label>
        <label>UUID филиала (необязательно) <input value={location} onChange={e => setLocation(e.target.value)} /></label>
        <button disabled={busy || !uuid.test(user.trim()) || !!location.trim() && !uuid.test(location.trim())} type="submit">
          Назначить</button>
      </form>
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
