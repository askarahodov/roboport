import React from 'react';

type Service = { id: string; code: string; title: string; durationMinutes: number };
type State = 'idle' | 'loading' | 'ready' | 'denied' | 'error';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function parseServices(value: unknown): Service[] {
  if (!value || typeof value !== 'object' || !('items' in value) ||
      !Array.isArray(value.items) || value.items.length > 100 ||
      !value.items.every((item: unknown) => item !== null && typeof item === 'object'
        && 'id' in item && typeof item.id === 'string' && uuid.test(item.id)
        && 'code' in item && typeof item.code === 'string'
        && 'title' in item && typeof item.title === 'string'
        && 'durationMinutes' in item && typeof item.durationMinutes === 'number' && Number.isInteger(item.durationMinutes)
        && item.durationMinutes >= 5 && item.durationMinutes <= 1440)) {
    throw new Error('Invalid catalog response');
  }
  return value.items as Service[];
}

export function AutoOrderServices() {
  const [organization, setOrganization] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [order, setOrder] = React.useState('');
  const [status, setStatus] = React.useState<State>('idle');
  const [catalog, setCatalog] = React.useState<Service[]>([]);
  const [selected, setSelected] = React.useState<Service[]>([]);
  const [loadedScope, setLoadedScope] = React.useState('');
  const [revision, setRevision] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState('');
  const valid = [organization, location, order].every(value => uuid.test(value.trim()));
  const scopeKey = [organization.trim(), location.trim(), order.trim()].join(':');
  const readyForScope = status === 'ready' && loadedScope === scopeKey;
  const base = '/api/v1/dev/auto/organizations/' + encodeURIComponent(organization.trim())
    + '/locations/' + encodeURIComponent(location.trim());
  const orderPath = base + '/orders/' + encodeURIComponent(order.trim()) + '/services';

  React.useEffect(() => {
    setCatalog([]); setSelected([]); setLoadedScope(''); setMessage('');
    if (!valid) { setStatus('idle'); return; }
    const controller = new AbortController();
    setStatus('loading');
    async function load() {
      try {
        const paths = [base + '/service-catalog', orderPath];
        const responses = await Promise.all(paths.map(path => fetch(path, {
          credentials: 'include', cache: 'no-store', signal: controller.signal,
        })));
        if (controller.signal.aborted) return;
        if (responses.some(response => [401, 403, 404].includes(response.status))) {
          setStatus('denied'); return;
        }
        if (responses.some(response => !response.ok)) throw new Error('Read error');
        const data = await Promise.all(responses.map(response => response.json() as Promise<unknown>));
        const available = parseServices(data[0]);
        const assigned = parseServices(data[1]);
        if (controller.signal.aborted) return;
        setCatalog(available); setSelected(assigned);
        setLoadedScope(scopeKey); setStatus('ready');
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    }
    void load();
    return () => controller.abort();
  }, [organization, location, order, revision, valid]);

  async function add(serviceId: string) {
    if (!valid || !readyForScope || busy || !catalog.some(x => x.id === serviceId)) return;
    setBusy(true); setMessage('');
    try {
      const csrf = await fetch('/api/v1/auth/csrf', {
        method: 'POST', credentials: 'include', cache: 'no-store',
      });
      if (!csrf.ok) throw new Error('CSRF unavailable');
      const value: unknown = await csrf.json();
      if (!value || typeof value !== 'object' || !('csrfToken' in value)
          || typeof value.csrfToken !== 'string') throw new Error('Invalid CSRF');
      const response = await fetch(orderPath, {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': value.csrfToken },
        body: JSON.stringify({ serviceId }),
      });
      if ([401, 403, 404].includes(response.status)) {
        setCatalog([]); setSelected([]); setStatus('denied'); return;
      }
      if (!response.ok) {
        setMessage(response.status === 409 ? 'Конфликт выбора услуги.' :
          'Не удалось добавить услугу. Проверьте полномочия и актуальность записи.');
        return;
      }
      setRevision(n => n + 1);
    } catch {
      setCatalog([]); setSelected([]);
      setMessage('Результат добавления неизвестен. Обновите список до повтора: повторный выбор не создаст дубликат.');
      setStatus('error');
    } finally {
      setBusy(false);
    }
  }

  const selectedIds = new Set(selected.map(x => x.id));
  return <section aria-label="Услуги автомобильного заказ-наряда (dev)"
      style={{ maxWidth: '76rem', margin: '2rem auto' }}>
    <h1>Услуги заказ-наряда (dev)</h1>
    <p>Только синтетический стенд. Выбор услуги не означает согласование цены,
      выполнение ремонта или начисление оплаты.</p>
    <label>UUID организации <input aria-label="UUID организации" disabled={busy} value={organization}
      onChange={e => setOrganization(e.target.value)} /></label>
    <label>UUID филиала <input aria-label="UUID филиала" disabled={busy} value={location}
      onChange={e => setLocation(e.target.value)} /></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" disabled={busy} value={order}
      onChange={e => setOrder(e.target.value)} /></label>
    {status === 'idle' && <p>Укажите организацию, филиал и заказ-наряд.</p>}
    {status === 'loading' && <p role="status">Загрузка услуг…</p>}
    {status === 'denied' && <p role="alert">Доступ запрещён, запись отменена
      или связь с автомобилем больше не подтверждена.</p>}
    {status === 'error' && <div><p role="alert">Не удалось загрузить услуги. {message}</p>
      <button type="button" onClick={() => setRevision(n => n + 1)}>Повторить загрузку</button></div>}
    {readyForScope && <>
      <h2>Уже выбранные услуги</h2>
      {selected.length === 0 ? <p>В заказ-наряде пока нет выбранных услуг.</p>
        : <ul>{selected.map(item => <li key={item.id}>
          {item.code} — {item.title} ({item.durationMinutes} мин)
        </li>)}</ul>}
      <h2>Каталог филиала</h2>
      {catalog.length === 0 ? <p>В филиале пока нет доступных услуг.</p>
        : <ul>{catalog.map(item => <li key={item.id}>
          {item.code} — {item.title} ({item.durationMinutes} мин)
          {' '}<button type="button" disabled={busy || selectedIds.has(item.id)}
            onClick={() => void add(item.id)}>
            {selectedIds.has(item.id) ? 'Уже добавлена' : 'Добавить услугу'}
          </button>
        </li>)}</ul>}
      {message && <p role="alert">{message}</p>}
      <button type="button" disabled={busy}
        onClick={() => setRevision(n => n + 1)}>Обновить список</button>
    </>}
  </section>;
}
