import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const reasons:Record<string,string>={PERIODIC:'Плановое ТО',DEFERRED:'Отложенная работа',FOLLOWUP:'Повторная проверка'};
type Mode='ALL'|'OVERDUE'|'UPCOMING';
type Item={recommendationId:string;workOrderId:string;serviceCode:string;title:string;
  reasonCode:string;baselineKm:number;dueKm:number|null;dueDate:string;status:'OVERDUE'|'UPCOMING'};
type Page={localDay:string;timeZone:string;items:Item[];nextCursor:string|null};
const day=(x:unknown):x is string=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&
  Number.isFinite(Date.parse(x+'T00:00:00Z'))&&new Date(x+'T00:00:00Z').toISOString().slice(0,10)===x;
const km=(x:unknown):x is number=>typeof x==='number'&&Number.isInteger(x)&&x>=0&&x<=2000000;
function parse(raw:unknown,org:string,loc:string,mode:Mode,horizon:number):Page{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const p=raw as Page&{syntheticOnly:boolean;sendAllowed:boolean;basis:string;organizationId:string;
    locationId:string;mode:string;horizonDays:number};
  if(p.syntheticOnly!==true||p.sendAllowed!==false||p.basis!=='UNBOOKED_ACTIVE_DUE_DATE'||
    p.organizationId!==org.toLowerCase()||p.locationId!==loc.toLowerCase()||p.mode!==mode||p.horizonDays!==horizon||
    !day(p.localDay)||typeof p.timeZone!=='string'||!p.timeZone||!Array.isArray(p.items)||p.items.length>25||
    !(p.nextCursor===null||(typeof p.nextCursor==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(p.nextCursor)&&p.items.length===25)))
    throw Error('shape');
  const end=Date.parse(p.localDay+'T00:00:00Z')+horizon*86400000;
  if(!p.items.every(x=>x&&uuid.test(x.recommendationId)&&uuid.test(x.workOrderId)&&
    typeof x.serviceCode==='string'&&typeof x.title==='string'&&Object.hasOwn(reasons,x.reasonCode)&&
    km(x.baselineKm)&&(x.dueKm===null||(km(x.dueKm)&&x.dueKm>x.baselineKm))&&day(x.dueDate)&&
    Date.parse(x.dueDate+'T00:00:00Z')<=end&&x.status===(x.dueDate<p.localDay?'OVERDUE':'UPCOMING')&&
    (mode==='ALL'||mode===x.status))||
    new Set(p.items.map(x=>x.recommendationId)).size!==p.items.length)throw Error('row');
  return p;
}
export function AutoMaintenanceQueue({org,loc}:{org:string;loc:string}){
  const [mode,setMode]=React.useState<Mode>('ALL');
  const [horizon,setHorizon]=React.useState(30);
  const [page,setPage]=React.useState<Page|null>(null);
  const [cursor,setCursor]=React.useState<string|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  function reset(){active.current?.abort();active.current=null;setPage(null);setCursor(null);setStatus('idle');}
  async function load(position:string|null=null){
    if(!uuid.test(org)||!uuid.test(loc))return;
    active.current?.abort();const control=new AbortController();active.current=control;
    setPage(null);setCursor(null);setStatus('loading');
    const query=new URLSearchParams({mode,horizon:String(horizon)});
    if(position)query.set('cursor',position);
    try{
      const response=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
        '/locations/'+encodeURIComponent(loc)+'/crm/maintenance-queue?'+query,
        {credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(!response.ok)throw Error('http');
      const result=parse(await response.json(),org,loc,mode,horizon);
      if(control.signal.aborted)return;
      if(position&&position===result.nextCursor)throw Error('cursor');
      setPage(result);setCursor(position);setStatus('ready');
    }catch{if(!control.signal.aborted)setStatus('error');}
  }
  return <section aria-label="Очередь планового ТО">
    <h2>Очередь планового ТО</h2>
    <p>Активные рекомендации без действующей повторной записи. Только календарный срок,
      исходный пробег не является текущим. Отправки напоминаний нет.</p>
    <label>Срок рекомендаций <select aria-label="Срок рекомендаций" value={mode}
      onChange={e=>{reset();setMode(e.target.value as Mode);}}>
      <option value="ALL">Все к сроку</option><option value="OVERDUE">Просроченные</option>
      <option value="UPCOMING">Предстоящие</option></select></label>
    <label>Горизонт ТО <select aria-label="Горизонт ТО" value={horizon}
      onChange={e=>{reset();setHorizon(Number(e.target.value));}}>
      {[7,30,90].map(n=><option key={n} value={n}>{n} дней</option>)}</select></label>
    <button type="button" disabled={!uuid.test(org)||!uuid.test(loc)||status==='loading'}
      onClick={()=>void load()}>Загрузить очередь ТО</button>
    {status==='loading'&&<p role="status">Чтение рекомендаций…</p>}
    {status==='denied'&&<p role="alert">Доступ к очереди ТО запрещён.</p>}
    {status==='error'&&<p role="alert">Очередь недоступна или срок страницы изменился. Загрузите сначала.</p>}
    {status==='ready'&&page&&<>
      <p>Календарный день: {page.localDay}. Часовой пояс: {page.timeZone}.</p>
      {page.items.length===0?<p>Рекомендаций к сроку нет.</p>:<div style={{overflowX:'auto'}}><table>
        <caption>Рекомендации без повторной записи</caption>
        <thead><tr><th>Срок</th><th>Услуга</th><th>Причина</th><th>Пробег, км</th><th>Исходный наряд</th></tr></thead>
        <tbody>{page.items.map(x=><tr key={x.recommendationId}>
          <td>{x.dueDate} · {x.status==='OVERDUE'?'Просрочено':'Предстоит'}</td>
          <td>{x.serviceCode} · {x.title}</td><td>{reasons[x.reasonCode]}</td>
          <td>{x.baselineKm.toLocaleString('ru-RU')} → {x.dueKm===null?'Не задан':x.dueKm.toLocaleString('ru-RU')}</td>
          <td style={{overflowWrap:'anywhere'}}>{x.workOrderId}</td></tr>)}</tbody></table></div>}
      {cursor&&<button type="button" onClick={()=>void load()}>Первая страница ТО</button>}
      {page.nextCursor&&<button type="button" onClick={()=>void load(page.nextCursor)}>Следующая страница ТО</button>}
    </>}
  </section>;
}
