import React from 'react';
import { Alert, EmptyState, Skeleton, Badge } from '../../ui';

type Ticket = { id: string; state: string; version: number };
type Event = {fromState:string;toState:string;version:number;at:string};
function TicketTimeline({ticketId}:{ticketId:string}){
 const [expanded,setExpanded]=React.useState(false);
 const [status,setStatus]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
 const [events,setEvents]=React.useState<Event[]>([]);
 const [retry,setRetry]=React.useState(0);
 React.useEffect(()=>{
   if(!expanded)return;
   const ctrl=new AbortController();
   setStatus('loading');setEvents([]);
   void (async()=>{try{
    const r=await fetch('/api/v1/dev/account/tickets/'+encodeURIComponent(ticketId)+'/timeline',
      {credentials:'include',cache:'no-store',signal:ctrl.signal});
    if([401,403,404].includes(r.status)){setStatus('denied');return;}
    if(!r.ok)throw Error('timeline');
    const data:unknown=await r.json();
    if(!data||typeof data!=='object'||!('items' in data)||!Array.isArray(data.items)
       ||!data.items.every((x:unknown)=>x&&typeof x==='object'
         &&'toState' in x&&typeof x.toState==='string'
         &&'at' in x&&typeof x.at==='string'))throw Error('invalid timeline');
    if(!ctrl.signal.aborted){setEvents(data.items as Event[]);setStatus('ready');}
   }catch{if(!ctrl.signal.aborted)setStatus('error');}})();
   return()=>ctrl.abort();
 },[ticketId,expanded,retry]);
 return <div className="account-timeline">
   <button type="button" aria-expanded={expanded} onClick={()=>setExpanded(x=>!x)}>
     {expanded?'Скрыть историю':'История этапов'}
   </button>
   {expanded&&<div className="account-timeline-panel" aria-label="История этапов заявки">
     {status==='loading'&&<Skeleton label="Загрузка истории…" />}
     {status==='denied'&&<Alert>История недоступна: права изменились.</Alert>}
     {status==='error'&&<><Alert>Не удалось загрузить историю.</Alert><button type="button" onClick={()=>setRetry(x=>x+1)}>Повторить историю</button></>}
     {status==='ready'&&(events.length
        ?<ol>{events.map((e,i)=><li key={i}><strong>{e.toState}</strong> · {new Date(e.at).toLocaleDateString('ru-RU')} · версия {e.version}</li>)}</ol>
        :<p role="status">История переходов пока пуста.</p>)}
   </div>}
 </div>;
}


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
    ? <ul className="account-records">{items.map(ticket => <li className="account-record" key={ticket.id}><span>Заявка <span className="account-record-id" title={ticket.id}>{ticket.id.slice(0,8)}</span></span><Badge>{ticket.state}</Badge><span>Версия {ticket.version}</span><TicketTimeline ticketId={ticket.id} /></li>)}</ul>
    : <EmptyState title="У вас пока нет доступных заявок." description="Когда появятся обращения, их статус будет виден здесь." />;
}
