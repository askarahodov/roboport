import React from 'react';

type Ticket = { id: string; state: string; version: number };

export function CustomerTickets() {
  const [items, setItems] = React.useState<Ticket[]>([]);
  const [status, setStatus] = React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [reload, setReload] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    setItems([]);
    async function load() {
      try {
        const response = await fetch('/api/v1/dev/account/tickets?limit=20&offset=0', {
          signal: controller.signal, credentials: 'include', cache: 'no-store',
        });
        if (response.status === 401 || response.status === 403 || response.status === 404) {
          setStatus('denied');
          return;
        }
        if (!response.ok) throw new Error('list failed');
        const body: unknown = await response.json();
        if (!body || typeof body !== 'object' || !('items' in body) ||
            !Array.isArray(body.items) ||
            !body.items.every((item: unknown) => item !== null && typeof item === 'object'
              && 'id' in item && typeof item.id === 'string'
              && 'state' in item && typeof item.state === 'string'
              && 'version' in item && typeof item.version === 'number')) {
          throw new Error('invalid response');
        }
        if (!controller.signal.aborted) {
          setItems(body.items as Ticket[]);
          setStatus('ready');
        }
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    }
    void load();
    return () => controller.abort();
  }, [reload]);

  if (status === 'loading') return <p role="status">Загрузка ваших заявок…</p>;
  if (status === 'denied') return <p role="alert">Доступ к заявкам закрыт или сессия истекла. Повторно войдите в кабинет.</p>;
  if (status === 'error') return <div><p role="alert">Не удалось загрузить заявки.</p>
    <button type="button" onClick={() => setReload(n => n+1)}>Повторить загрузку</button></div>;
  return items.length > 0
    ? <ul>{items.map(ticket => <li key={ticket.id}>Заявка {ticket.id}, статус: {ticket.state}, версия {ticket.version}</li>)}</ul>
    : <p role="status">У вас пока нет доступных заявок.</p>;
}
