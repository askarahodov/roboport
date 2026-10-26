import React from 'react';
import { Alert, EmptyState, Skeleton, Badge } from '../../ui';

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

  if (status === 'loading') return <Skeleton label="Загрузка ваших заявок…" />;
  if (status === 'denied') return <Alert>Доступ к заявкам закрыт или сессия истекла. Повторно войдите в кабинет.</Alert>;
  if (status === 'error') return <div><p role="alert">Не удалось загрузить заявки.</p>
    <button type="button" onClick={() => setReload(n => n+1)}>Повторить загрузку</button></div>;
  return items.length > 0
    ? <ul className="account-records">{items.map(ticket => <li className="account-record" key={ticket.id}><span>Заявка <span className="account-record-id" title={ticket.id}>{ticket.id.slice(0,8)}</span></span><Badge>{ticket.state}</Badge><span>Версия {ticket.version}</span></li>)}</ul>
    : <EmptyState title="У вас пока нет доступных заявок." description="Когда появятся обращения, их статус будет виден здесь." />;
}
