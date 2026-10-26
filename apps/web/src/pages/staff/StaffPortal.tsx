import React from 'react';
import { TicketWorkflow } from './TicketWorkflow';
import { StaffEstimates, StaffStock } from './StaffOperations';
import { StaffAudit } from './StaffAudit';
import { StaffGuestClaims } from './StaffGuestClaims';
import { PageShell, Card, Alert, Skeleton, EmptyState, Badge, Button } from '../../ui';
import './staff.css';

type Role = 'INTAKE' | 'ENGINEER' | 'STOCK' | 'MANAGER' | 'FINANCE' | 'AUDITOR';
const sections: {label:string; roles:Role[]}[] = [
  {label:'Очередь',roles:['INTAKE','ENGINEER','MANAGER']},
  {label:'Приёмка',roles:['INTAKE']},
  {label:'Диагностика',roles:['ENGINEER']},
  {label:'Сметы',roles:['MANAGER','FINANCE']},
  {label:'Склад',roles:['STOCK']},
  {label:'QC и выдача',roles:['ENGINEER','MANAGER']},
  {label:'Аудит',roles:['AUDITOR']},
];

function StaffQueue({roles}:{roles:Role[]}) {
  const [items,setItems]=React.useState<{id:string;state:string;version:number}[]>([]);
  const [status,setStatus]=React.useState<'loading'|'ready'|'error'|'denied'>('loading');
  const [retry,setRetry]=React.useState(0);
  const [query,setQuery]=React.useState('');
  const [filter,setFilter]=React.useState('ALL');
  const [sort,setSort]=React.useState<'state'|'version'>('state');
  React.useEffect(()=>{
    const controller=new AbortController();
    setStatus('loading');setItems([]);
    (async()=>{
      try {
        const res=await fetch('/api/v1/dev/staff/queue?limit=20&offset=0',
          {credentials:'include',cache:'no-store',signal:controller.signal});
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw new Error('queue error');
        const body:unknown=await res.json();
        if(!body||typeof body!=='object'||!('items' in body)||!Array.isArray(body.items)
          || !body.items.every((x:unknown)=>x&&typeof x==='object'
            && 'id' in x && typeof x.id==='string'
            && 'state' in x && typeof x.state==='string'
            && 'version' in x && typeof x.version==='number'))throw new Error('invalid queue');
        if(!controller.signal.aborted){setItems(body.items);setStatus('ready');}
      }catch{if(!controller.signal.aborted)setStatus('error');}
    })();
    return ()=>controller.abort();
  },[retry]);
  const states=Array.from(new Set(items.map(t=>t.state))).sort();
  const visible=items.filter(t=>(filter==='ALL'||t.state===filter) &&
    (!query.trim()||t.id.toLowerCase().includes(query.trim().toLowerCase())))
    .sort((a,b)=>sort==='state'?a.state.localeCompare(b.state):b.version-a.version);
  return <section className="crm-section" aria-label="Назначенные заявки">
    <h3>Назначенные заявки</h3>
    {status==='loading'&&<Skeleton label="Загрузка очереди…" />}
    {status==='denied'&&<Alert>Доступ к очереди отозван.</Alert>}
    {status==='error'&&<><Alert>Ошибка загрузки очереди.</Alert>
      <button type="button" onClick={()=>setRetry(n=>n+1)}>Повторить очередь</button></>}
    {status==='ready'&&<div className="crm-filters"><label>Поиск по номеру <input placeholder="UUID заявки" value={query} onChange={e=>setQuery(e.target.value)} /></label><label>Статус <select value={filter} onChange={e=>setFilter(e.target.value)}><option value="ALL">Все статусы</option>{states.map(x=><option key={x} value={x}>{x}</option>)}</select></label><label>Сортировка <select value={sort} onChange={e=>setSort(e.target.value as 'state'|'version')}><option value="state">По статусу</option><option value="version">По версии</option></select></label></div>}
    {status==='ready'&&(visible.length?
      <ul className="crm-ticket-grid">{visible.map(t=><li className="crm-ticket" key={t.id}><div className="crm-ticket-head"><div><span className="crm-ticket-label">ЗАЯВКА</span><h4 title={t.id}>{t.id.slice(0,8)}…</h4></div><Badge tone={t.state==='SAFETY_HOLD'||t.state==='QC_FAILED'?'error':'neutral'}>{t.state}</Badge></div><p>Версия {t.version} · серверное назначение</p><TicketWorkflow ticket={t} roles={roles} onChanged={()=>setRetry(n=>n+1)}/></li>)}</ul>:
      <EmptyState title={items.length?"По фильтру заявок нет.":"Назначенных заявок нет."} description="Отображаются только заявки, разрешённые сервером." />)}
  </section>;
}

export function StaffPortal() {
  const [state,setState]=React.useState<'loading'|'ready'|'login'|'forbidden'|'error'>('loading');
  const [roles,setRoles]=React.useState<Role[]>([]);
  const [attempt,setAttempt]=React.useState(0);
  React.useEffect(()=>{
    const controller=new AbortController();
    setState('loading');setRoles([]);
    (async()=>{
      try {
        const res=await fetch('/api/v1/dev/staff/access', {credentials:'include',cache:'no-store',signal:controller.signal});
        if(controller.signal.aborted)return;
        if(res.status===401){setState('login');return;}
        if(res.status===403||res.status===404){setState('forbidden');return;}
        if(!res.ok)throw new Error('access unavailable');
        const body:unknown=await res.json();
        const permitted=['INTAKE','ENGINEER','STOCK','MANAGER','FINANCE','AUDITOR'];
        if(!body||typeof body!=='object'||!('roles' in body)||!Array.isArray(body.roles)
          || !body.roles.every((role:unknown)=>typeof role==='string'&&permitted.includes(role))) {
          throw new Error('invalid access response');
        }
        if(!controller.signal.aborted){setRoles(body.roles as Role[]);setState('ready');}
      }catch{if(!controller.signal.aborted)setState('error');}
    })();
    return ()=>controller.abort();
  },[attempt]);
  const allowed=sections.filter(x=>x.roles.some(role=>roles.includes(role)));
  const [active,setActive]=React.useState('Очередь');
  const selected=allowed.some(x=>x.label===active)?active:allowed[0]?.label;
  const hasQueue=roles.some(r=>['INTAKE','ENGINEER','MANAGER'].includes(r));
  return <PageShell className="crm-workspace">
    <div className="crm-heading"><div><span className="ui-eyebrow">ROBOport / SERVICE OPERATIONS</span><h1>Рабочее пространство</h1><p>Обслуживание роботов · синтетический стенд, реальные операции отключены</p></div><Badge tone="warning">DEV / TEST</Badge></div>
    {state==='loading'&&<Skeleton label="Проверка прав сотрудника…" />}
    {state==='login'&&<Alert>401 — требуется локальный вход. <a href="/account/login">Перейти ко входу</a></Alert>}
    {state==='forbidden'&&<Alert>403 — раздел недоступен: нет подтверждённых прав.</Alert>}
    {state==='error'&&<div><Alert>Не удалось проверить права на сервере.</Alert><Button onClick={()=>setAttempt(a=>a+1)}>Повторить</Button></div>}
    {state==='ready'&&<div className="crm-layout">
      <aside className="crm-rail" aria-label="Разделы CRM"><div className="crm-rail-brand">ROBO<span>port</span><small>STAFF CONSOLE</small></div><h2>Доступные разделы</h2>
        <nav aria-label="Навигация CRM" className="crm-menu">{allowed.map(section=><button type="button" key={section.label} aria-current={selected===section.label?'page':undefined} onClick={()=>setActive(section.label)}>{section.label}<span aria-hidden="true">↗</span></button>)}</nav>
        <p className="crm-security">Права и доступ к каждой заявке проверяются сервером.</p>
      </aside>
      <div className="crm-main">
        <Card className="crm-summary"><div><span className="ui-eyebrow">РАБОЧАЯ ПАНЕЛЬ</span><h2>{selected}</h2><p>Доступ: {roles.join(' · ')}</p></div><Badge tone="success">ACL active</Badge></Card>
        {hasQueue&&['Очередь','Приёмка','Диагностика','QC и выдача'].includes(selected||'')&&<StaffQueue roles={roles}/>}
        {selected==='Сметы'&&roles.some(r=>['FINANCE','MANAGER'].includes(r))&&<StaffEstimates />}
        {selected==='Склад'&&roles.includes('STOCK')&&<StaffStock />}
        {selected==='Аудит'&&roles.includes('AUDITOR')&&<StaffAudit />}
        {selected==='Приёмка'&&roles.includes('INTAKE')&&<StaffGuestClaims roles={roles}/>}
        {selected==='Очередь'&&roles.includes('MANAGER')&&<StaffGuestClaims roles={roles}/>}
      </div>
    </div>}
  </PageShell>;
}
