import React from 'react';
import { TicketWorkflow } from './TicketWorkflow';
import { StaffEstimates, StaffStock } from './StaffOperations';
import { StaffAudit } from './StaffAudit';

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
  return <section aria-label="Назначенные заявки">
    <h3>Назначенные заявки</h3>
    {status==='loading'&&<p role="status">Загрузка очереди…</p>}
    {status==='denied'&&<p role="alert">Доступ к очереди отозван.</p>}
    {status==='error'&&<><p role="alert">Ошибка загрузки очереди.</p>
      <button type="button" onClick={()=>setRetry(n=>n+1)}>Повторить очередь</button></>}
    {status==='ready'&&(items.length?
      <ul>{items.map(t=><li key={t.id}>Заявка {t.id}: {t.state}, версия {t.version}
        <TicketWorkflow ticket={t} roles={roles} onChanged={()=>setRetry(n=>n+1)}/></li>)}</ul>:
      <p role="status">Назначенных заявок нет.</p>)}
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
  return <section aria-live="polite">
    <h1>Портал сотрудников</h1>
    <p>Только синтетический стенд. Реальная приёмка и выдача отключены.</p>
    {state==='loading' && <p role="status">Проверка прав сотрудника…</p>}
    {state==='login' && <p role="alert">401 — требуется локальный вход.</p>}
    {state==='forbidden' && <p role="alert">403 — раздел недоступен: нет подтверждённых прав.</p>}
    {state==='error' && <div><p role="alert">Не удалось проверить права на сервере.</p>
      <button type="button" onClick={()=>setAttempt(a=>a+1)}>Повторить</button></div>}
    {state==='ready' && <>
      <h2>Доступные разделы</h2>
      <ul>{sections.filter(s=>s.roles.some(r=>roles.includes(r))).map(s=>
        <li key={s.label}>{s.label} — операции ещё не подключены</li>)}</ul>
      {roles.some(r=>['INTAKE','ENGINEER','MANAGER'].includes(r))&&<StaffQueue roles={roles}/>}
      {roles.some(r=>['FINANCE','MANAGER'].includes(r))&&<StaffEstimates />}
      {roles.includes('STOCK')&&<StaffStock />}
      {roles.includes('AUDITOR')&&<StaffAudit />}
      <p role="status">Сервер проверяет ACL отдельно для каждой операции.</p>
    </>}
  </section>;
}
