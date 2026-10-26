import React from 'react';
import { Alert, EmptyState, Skeleton } from '../../ui';
import { EstimateActions } from './EstimateActions';

type Section = 'robots' | 'estimates' | 'documents';
const labels: Record<Section,string> = {
  robots: 'роботов', estimates: 'смет', documents: 'документов',
};
type Entry = { id: string; label?: string | null; status?: string;
  version?: number; total_minor?: number; currency?: string;
  mime?: string; byte_size?: number };

export function CustomerSummary({ section }: { section: Section }) {
  const [items, setItems] = React.useState<Entry[]>([]);
  const [status, setStatus] = React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [retry, setRetry] = React.useState(0);
  React.useEffect(() => {
    const controller=new AbortController();
    setItems([]);
    setStatus('loading');
    async function load() {
      try {
        const res=await fetch('/api/v1/dev/account/summary/'+section, {
          credentials:'include', cache:'no-store', signal:controller.signal,
        });
        if ([401,403,404].includes(res.status)) { setStatus('denied'); return; }
        if (!res.ok) throw new Error('Summary error');
        const data:unknown=await res.json();
        if (!data || typeof data!=='object' || !('items' in data) ||
          !Array.isArray(data.items) || !data.items.every((item:unknown) =>
            item && typeof item==='object' && 'id' in item && typeof item.id==='string')) {
          throw new Error('Invalid summary');
        }
        if (!controller.signal.aborted) {
          setItems(data.items as Entry[]);
          setStatus('ready');
        }
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    }
    void load();
    return () => controller.abort();
  },[section,retry]);
  if (status==='loading') return <Skeleton label={"Загрузка ваших "+labels[section]+"…"} />;
  if (status==='denied') return <Alert>Доступ запрещён или серверная сессия отозвана.</Alert>;
  if (status==='error') return <div><p role="alert">Не удалось загрузить раздел.</p>
    <button type="button" onClick={() => setRetry(x => x+1)}>Повторить загрузку</button></div>;
  if (items.length===0) return <EmptyState title={"Доступных "+labels[section]+" пока нет."} description="Здесь появятся записи после подтверждения доступа." />;
  return <ul className="account-records">{items.map(item => <li className="account-record" key={item.id}>
    {section==='robots' && <>Робот {item.label || item.id}</>}
    {section==='estimates' && <>Смета {item.id}, версия {item.version}, статус {item.status},
      сумма {typeof item.total_minor==='number' ? (item.total_minor/100).toFixed(2) : '—'} {item.currency}
      <EstimateActions estimate={item} afterSuccess={()=>setRetry(x=>x+1)}/></>}
    {section==='documents' && <>Документ {item.id}, тип {item.mime}, размер {item.byte_size} байт.
      <a href={'/api/v1/dev/attachments/'+encodeURIComponent(item.id)+'/content'}>
        Открыть через защищённый сервер</a></>}
  </li>)}</ul>;
}
