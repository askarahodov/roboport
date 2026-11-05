import React from 'react';
import { StaffCrmContactPreferences } from './StaffCrmContactPreferences';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Event={id:string;actorId:string;stageCode:'QUALIFIED'|'BOOKED'|'LOST';
  reasonCode:string|null;appointmentId:string|null;createdAt:string};
type Lead={leadId:string;customerLinkId:string;ownerId:string;
  sourceCode:'WEB'|'PHONE'|'WALK_IN';status:'NEW'|'QUALIFIED'|'BOOKED'|'LOST';
  createdAt:string;syntheticOnly:true;events:Event[]};
function parseLead(v:unknown):Lead{
  if(!v||typeof v!=='object')throw Error('shape');
  const x=v as Lead;
  if(![x.leadId,x.customerLinkId,x.ownerId].every(y=>typeof y==='string'&&uuid.test(y))||
    !['WEB','PHONE','WALK_IN'].includes(x.sourceCode)||
    !['NEW','QUALIFIED','BOOKED','LOST'].includes(x.status)||
    x.syntheticOnly!==true||!Array.isArray(x.events)||x.events.length>99||
    !x.events.every(e=>e&&uuid.test(e.id)&&uuid.test(e.actorId)&&
      ['QUALIFIED','BOOKED','LOST'].includes(e.stageCode)&&
      (e.reasonCode===null||['PRICE','TIMING','NO_RESPONSE','OTHER'].includes(e.reasonCode))&&
      (e.appointmentId===null||uuid.test(e.appointmentId))))throw Error('shape');
  return x;
}
type FollowupEvent={id:string;actorId:string;action:'DONE'|'CANCELED';createdAt:string};
type Followup={taskId:string;ownerId:string;creatorId:string;
  taskCode:'CALL_BACK'|'CHECK_BOOKING'|'SERVICE_REVIEW';dueAt:string;createdAt:string;
  status:'OPEN'|'DONE'|'CANCELED';event:FollowupEvent|null;originWorkOrderId?:string|null};
const leadSourceLabels:Record<Lead['sourceCode'],string>={
  WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение',
};
const leadStatusLabels:Record<Lead['status'],string>={
  NEW:'Новое',QUALIFIED:'Обработано',BOOKED:'Записан',LOST:'Отказ',
};
const leadStageLabels:Record<Event['stageCode'],string>={
  QUALIFIED:'Обработано',BOOKED:'Записан',LOST:'Отказ',
};
const leadReasonLabels:Record<string,string>={
  PRICE:'Стоимость',TIMING:'Время',NO_RESPONSE:'Нет ответа',OTHER:'Другое',
};
const taskCodeLabels:Record<Followup['taskCode'],string>={
  CALL_BACK:'Связаться позже',CHECK_BOOKING:'Проверить запись',
  SERVICE_REVIEW:'Проверить запрос на обслуживание',
};
const taskStatusLabels:Record<Followup['status'],string>={
  OPEN:'Открыта',DONE:'Выполнена',CANCELED:'Отменена',
};
type InboxTask=Followup&{leadId:string};
type InboxPage={items:InboxTask[];nextCursor:string|null};
function parseInbox(v:unknown):InboxPage{
  if(!v||typeof v!=='object')throw Error('inbox');
  const x=v as {syntheticOnly?:unknown;items?:unknown;nextCursor?:unknown};
  if(x.syntheticOnly!==true||!Array.isArray(x.items)||x.items.length>25||
     !(x.nextCursor===null||(typeof x.nextCursor==='string'&&
       x.nextCursor.length>0&&x.nextCursor.length<=180&&
       /^[A-Za-z0-9_-]+$/.test(x.nextCursor))))
    throw Error('inbox');
  const items=x.items.map((item:unknown)=>{
    if(!item||typeof item!=='object'||!('leadId' in item)||
       typeof item.leadId!=='string'||!uuid.test(item.leadId))throw Error('inbox');
    const task=parseFollowup(item);
    if(task.status!=='OPEN'||task.event!==null)throw Error('inbox');
    return {...task,leadId:item.leadId};
  });
  return {items,nextCursor:x.nextCursor as string|null};
}
type PendingTaskRequest={url:string;body:Record<string,string>;scope:string;leadId:string};
type PendingLeadRequest={url:string;body:Record<string,unknown>;scope:string;leadId:string|null};
const validIso=(v:unknown):v is string=>typeof v==='string'&&
  !Number.isNaN(Date.parse(v));
function parseFollowup(v:unknown):Followup{
  if(!v||typeof v!=='object')throw Error('task');
  const t=v as Followup;
  if(![t.taskId,t.ownerId,t.creatorId].every(x=>typeof x==='string'&&uuid.test(x))||
    !['CALL_BACK','CHECK_BOOKING','SERVICE_REVIEW'].includes(t.taskCode)||
    !validIso(t.dueAt)||!validIso(t.createdAt)||
    !(t.originWorkOrderId==null||(typeof t.originWorkOrderId==='string'&&uuid.test(t.originWorkOrderId)))||
    !['OPEN','DONE','CANCELED'].includes(t.status)||
    (t.event===null?t.status!=='OPEN':
      !t.event||!uuid.test(t.event.id)||!uuid.test(t.event.actorId)||
      !validIso(t.event.createdAt)||t.event.action!==t.status))throw Error('task');
  return t;
}
function parseFollowups(v:unknown):Followup[]{
  if(!v||typeof v!=='object')throw Error('tasks');
  const obj=v as {syntheticOnly?:unknown;items?:unknown};
  if(obj.syntheticOnly!==true||!Array.isArray(obj.items)||obj.items.length>100)
    throw Error('tasks');
  return obj.items.map(parseFollowup);
}
type LeadPage={items:Lead[];nextCursor:string|null};
function parseList(v:unknown):LeadPage{
  if(!v||typeof v!=='object'||!('syntheticOnly' in v)||
    v.syntheticOnly!==true||!('items' in v)||
    !Array.isArray(v.items)||v.items.length>25)throw Error('shape');
  const next='nextCursor' in v?v.nextCursor:null;
  if(!(next===null||(typeof next==='string'&&next.length>0&&
       next.length<=180&&/^[A-Za-z0-9_-]+$/.test(next))))throw Error('shape');
  return {items:v.items.map(parseLead),nextCursor:next};
}
async function token(){
  const res=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',
    cache:'no-store'});
  if(!res.ok)throw Error('csrf');
  const v:unknown=await res.json();
  if(!v||typeof v!=='object'||!('csrfToken' in v)||
    typeof v.csrfToken!=='string')throw Error('csrf');
  return v.csrfToken;
}
export function AutoCrmLeads(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [link,setLink]=React.useState('');
  const [source,setSource]=React.useState<'WEB'|'PHONE'|'WALK_IN'>('WALK_IN');
  const [stage,setStage]=React.useState<'QUALIFIED'|'BOOKED'|'LOST'>('QUALIFIED');
  const [reason,setReason]=React.useState('TIMING');
  const [appointment,setAppointment]=React.useState('');
  const [selected,setSelected]=React.useState('');
  const [rows,setRows]=React.useState<Lead[]>([]);
  const [leadCursor,setLeadCursor]=React.useState<string|null>(null);
  const [leadHistory,setLeadHistory]=React.useState<(string|null)[]>([]);
  const [leadNext,setLeadNext]=React.useState<string|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [message,setMessage]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const [busy,setBusy]=React.useState(false);
  const [tasks,setTasks]=React.useState<Followup[]>([]);
  const [taskStatus,setTaskStatus]=React.useState<'idle'|'loading'|'ready'|'error'>('idle');
  const [taskRevision,setTaskRevision]=React.useState(0);
  const [taskCode,setTaskCode]=React.useState<Followup['taskCode']>('CALL_BACK');
  const [taskDue,setTaskDue]=React.useState('');
  const [reviewOrder,setReviewOrder]=React.useState('');
  const [reviewDue,setReviewDue]=React.useState('');
  const [taskBusy,setTaskBusy]=React.useState(false);
  const [taskMessage,setTaskMessage]=React.useState('');
  const [inbox,setInbox]=React.useState<InboxTask[]>([]);
  const [inboxStatus,setInboxStatus]=React.useState<'idle'|'loading'|'ready'|'error'>('idle');
  const [inboxRevision,setInboxRevision]=React.useState(0);
  const [inboxCursor,setInboxCursor]=React.useState<string|null>(null);
  const [inboxHistory,setInboxHistory]=React.useState<(string|null)[]>([]);
  const [inboxNext,setInboxNext]=React.useState<string|null>(null);
  const [inboxOpening,setInboxOpening]=React.useState(false);
  const [inboxMessage,setInboxMessage]=React.useState('');
  const [pendingTask,setPendingTask]=React.useState<PendingTaskRequest|null>(null);
  const [pendingLead,setPendingLead]=React.useState<PendingLeadRequest|null>(null);
  const valid=uuid.test(org)&&uuid.test(loc);
  const scope=org+':'+loc;
  const scopeRef=React.useRef(scope);
  scopeRef.current=scope;
  const [loadedScope,setLoadedScope]=React.useState('');
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/crm/leads';
  React.useEffect(()=>{
    // Keep the selected lead during same-branch refresh (e.g. after a
    // stage change). Clearing on scope changes is immediate in inputs below.
    setRows([]);setStatus('idle');setLoadedScope('');
    if(!valid)return;
    const controller=new AbortController();
    setStatus('loading');
    void (async()=>{
      try{
        const res=await fetch(base+
          (leadCursor?'?cursor='+encodeURIComponent(leadCursor):''),{
            credentials:'include',cache:'no-store',signal:controller.signal,
          });
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('request');
        const parsed=parseList(await res.json());
        if(controller.signal.aborted)return;
        setRows(parsed.items);setLeadNext(parsed.nextCursor);
        setLoadedScope(scope);setStatus('ready');
      }catch{if(!controller.signal.aborted)setStatus('error');}
    })();
    return ()=>controller.abort();
  },[org,loc,revision,valid,leadCursor]);
  React.useEffect(()=>{
    setTasks([]);setTaskStatus('idle');setTaskMessage('');
    if(!valid||!selected||loadedScope!==scope||status!=='ready')return;
    const controller=new AbortController();
    setTaskStatus('loading');
    void (async()=>{
      try{
        const res=await fetch(base+'/'+encodeURIComponent(selected)+'/tasks',{
          credentials:'include',cache:'no-store',signal:controller.signal,
        });
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){
          setTasks([]);setRows([]);setSelected('');setStatus('denied');return;
        }
        if(!res.ok)throw Error('request');
        const list=parseFollowups(await res.json());
        if(!controller.signal.aborted){setTasks(list);setTaskStatus('ready');}
      }catch{if(!controller.signal.aborted)setTaskStatus('error');}
    })();
    return ()=>controller.abort();
  },[org,loc,selected,loadedScope,status,taskRevision]);
  React.useEffect(()=>{
    setInbox([]);setInboxNext(null);setInboxStatus('idle');setInboxMessage('');
    if(!valid||loadedScope!==scope||status!=='ready')return;
    const controller=new AbortController();
    setInboxStatus('loading');
    void (async()=>{
      try{
        const response=await fetch('/api/v1/dev/auto/organizations/'+
          encodeURIComponent(org)+'/locations/'+encodeURIComponent(loc)+'/crm/inbox'+
          (inboxCursor?'?cursor='+encodeURIComponent(inboxCursor):''),{
            credentials:'include',cache:'no-store',signal:controller.signal,
          });
        if(controller.signal.aborted)return;
        if([401,403,404].includes(response.status)){
          setInbox([]);setRows([]);setSelected('');setStatus('denied');return;
        }
        if(!response.ok)throw Error('inbox');
        const page=parseInbox(await response.json());
        if(!controller.signal.aborted){setInbox(page.items);setInboxNext(page.nextCursor);setInboxStatus('ready');}
      }catch{if(!controller.signal.aborted)setInboxStatus('error');}
    })();
    return ()=>controller.abort();
  },[org,loc,valid,loadedScope,status,inboxRevision,inboxCursor,taskRevision,revision]);

  async function openInboxLead(leadId:string){
    if(inboxOpening||busy||taskBusy||pendingLead!==null||pendingTask!==null||
       status!=='ready'||loadedScope!==scope)return;
    const existing=rows.find(x=>x.leadId===leadId);
    if(existing){setSelected(leadId);setInboxMessage('');return;}
    setInboxOpening(true);setInboxMessage('');
    try{
      const response=await fetch(base+'/'+encodeURIComponent(leadId),{
        credentials:'include',cache:'no-store',
      });
      if(scopeRef.current!==scope)return;
      if([401,403,404].includes(response.status)){
        setInbox([]);setRows([]);setSelected('');setStatus('denied');return;
      }
      if(!response.ok)throw Error('lead');
      const lead=parseLead(await response.json());
      if(scopeRef.current!==scope)return;
      if(lead.leadId!==leadId)throw Error('lead');
      setRows(current=>[lead,...current.filter(x=>x.leadId!==leadId)].slice(0,50));
      setSelected(leadId);
    }catch{
      if(scopeRef.current===scope)setInboxMessage('Не удалось открыть обращение из очереди.');
    }finally{setInboxOpening(false);}
  }
  async function taskMutation(url:string,body:Record<string,string>,retry?:PendingTaskRequest){
    if(taskBusy||pendingLead!==null||loadedScope!==scope||status!=='ready'||!selected||
       (retry ? retry.scope!==scope||retry.leadId!==selected||
         pendingTask?.body.requestKey!==retry.body.requestKey :
         taskStatus!=='ready'||pendingTask!==null))return;
    const request=retry??{url,body:{...body,requestKey:crypto.randomUUID()},scope,leadId:selected};
    setTaskBusy(true);setTaskMessage('');
    let submitted=false;
    try{
      const csrf=await token();
      // Retain the exact payload even if the server commits but the response is lost.
      setPendingTask(request);
      submitted=true;
      const response=await fetch(request.url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify(request.body)});
      if([401,403,404].includes(response.status)){
        setPendingTask(null);setTasks([]);setRows([]);setSelected('');setStatus('denied');return;
      }
      if(response.status>=500){
        // Unknown server outcome: retain the exact request until an explicit replay.
        setTasks([]);setTaskStatus('error');
        setTaskMessage('Сервер не подтвердил результат. Повторите запрос с тем же ключом.');
        return;
      }
      if(!response.ok){
        setPendingTask(null);
        setTaskMessage(response.status===409?
          'Конфликт задачи. Обновите журнал перед новым действием.':
          'Нельзя сохранить задачу. Проверьте срок, этап и полномочия.');
        setTaskRevision(n=>n+1);
        return;
      }
      parseFollowup(await response.json());
      setPendingTask(null);
      setTaskMessage('Действие подтверждено в неизменяемом журнале.');
      setTaskRevision(n=>n+1);
      setInboxCursor(null);setInboxHistory([]);setInboxRevision(n=>n+1);
    }catch{
      if(!submitted){
        if(!retry){
          setPendingTask(null);
          setTaskMessage('Не удалось получить CSRF. Запрос не отправлен; обновите задачи и повторите действие.');
        }else{
          setTaskMessage('Не удалось обновить CSRF. Исходный запрос сохранён; повторите его с тем же ключом.');
        }
      }else{
        setTasks([]);setTaskStatus('error');
        setTaskMessage('Ответ не получен. Повторите тот же запрос с сохранённым ключом; новое действие заблокировано.');
      }
    }finally{setTaskBusy(false);}
  }
  async function mutate(url:string,body:Record<string,unknown>,retry?:PendingLeadRequest){
    if(busy||taskBusy||pendingTask!==null||status!=='ready'||loadedScope!==scope||
       (retry ? retry.scope!==scope||pendingLead?.body.requestKey!==retry.body.requestKey||
         (retry.leadId!==null&&selected!==retry.leadId) :
         pendingLead!==null))return;
    const request=retry??{url,body:{...body,requestKey:crypto.randomUUID()},
      scope,leadId:url.endsWith('/stages')?selected:null};
    setBusy(true);setMessage('');
    let submitted=false;
    try{
      const csrf=await token();
      setPendingLead(request);
      submitted=true;
      const r=await fetch(request.url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify(request.body)});
      if([401,403,404].includes(r.status)){
        setPendingLead(null);setPendingTask(null);setRows([]);setSelected('');setStatus('denied');return;
      }
      if(r.status>=500){
        setMessage('Сервер не подтвердил результат. Повторите запрос с тем же ключом.');
        return;
      }
      if(!r.ok){
        setPendingLead(null);
        setMessage(r.status===409?
          'Конфликт или повторная запись. Сверьте журнал перед новой операцией.':
          'Операция отклонена. Проверьте связь и права.');
        return;
      }
      const updated=parseLead(await r.json());
      setPendingLead(null);
      setRows(current=>{
        const rest=current.filter(x=>x.leadId!==updated.leadId);
        return [updated,...rest].slice(0,50);
      });
      setSelected(updated.leadId);
      setMessage('Изменение подтверждено. История обновлена.');
      setInboxCursor(null);setInboxHistory([]);setInboxRevision(n=>n+1);
    }catch{
      if(!submitted){
        if(!retry){
          setPendingLead(null);
          setMessage('Не удалось получить CSRF. Запрос не отправлен.');
        }else{
          setMessage('Не удалось обновить CSRF. Исходный запрос сохранён; повторите его с тем же ключом.');
        }
      }else{
        setMessage('Ответ не получен. Повторите запрос с тем же ключом; другие изменения заблокированы.');
      }
    }finally{setBusy(false);}
  }
  const chosen=rows.find(x=>x.leadId===selected);
  return <section aria-label="Синтетический реестр CRM (dev)">
    <h1>Обращения и этапы CRM</h1>
    <p>Только тестовый HTTPS-стенд. Один лид на подтверждённую связь,
      источник и филиал; никакой отправки сообщений, звонков или обработки
      реальных номеров. Этап «Записан» связывается с существующей записью,
      но не означает посещение или оплату.</p>
    <label>UUID организации <input aria-label="UUID организации" disabled={busy||taskBusy}
      value={org} onChange={e=>{setPendingTask(null);setPendingLead(null);setRows([]);setSelected('');setStatus('idle');setInboxCursor(null);setInboxHistory([]);setLeadCursor(null);setLeadHistory([]);setLeadNext(null);setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" disabled={busy||taskBusy}
      value={loc} onChange={e=>{setPendingTask(null);setPendingLead(null);setRows([]);setSelected('');setStatus('idle');setInboxCursor(null);setInboxHistory([]);setLeadCursor(null);setLeadHistory([]);setLeadNext(null);setLoc(e.target.value);}}/></label>
    {status==='idle'&&<p>Укажите организацию и филиал.</p>}
    {status==='loading'&&<p role="status">Чтение обращений…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<p role="alert">Не удалось загрузить обращения.</p>}
    {status==='ready'&&loadedScope===scope&&<>
      <button type="button" disabled={busy||taskBusy||pendingLead!==null||pendingTask!==null}
        onClick={()=>{setLeadCursor(null);setLeadHistory([]);setLeadNext(null);
          setSelected('');setRevision(n=>n+1);}}>
        Обновить список</button>
      <div aria-label="Очередь CRM">
        <h2>Очередь открытых задач филиала</h2>
        <p>Только внутренние задачи. Сначала просроченные, затем ближайшие по сроку.
          Видимость зависит от действующей роли и подтверждённой связи с автомобилем.</p>
        <button type="button" disabled={busy||taskBusy||inboxOpening}
          onClick={()=>{setInboxCursor(null);setInboxHistory([]);setInboxRevision(n=>n+1);}}>Обновить очередь</button>
        {inboxStatus==='loading'&&<p role="status">Чтение очереди…</p>}
        {inboxStatus==='error'&&<p role="alert">Не удалось загрузить очередь.
          Проверьте права и обновите очередь.</p>}
        {inboxStatus==='ready'&&inbox.length===0&&<p>Открытых задач нет.</p>}
        {inboxStatus==='ready'&&<ol>{inbox.map(task=><li key={task.taskId}>
          {taskCodeLabels[task.taskCode]} ·
          {new Date(task.dueAt).toLocaleString('ru-RU')} ·
          {Date.parse(task.dueAt)<Date.now()?'Просрочена':'Запланирована'}
          <button type="button"
            disabled={inboxOpening||busy||taskBusy||pendingLead!==null||pendingTask!==null}
            onClick={()=>void openInboxLead(task.leadId)}>Открыть обращение</button>
        </li>)}</ol>}
        {inboxStatus==='ready'&&<div>
          {inboxHistory.length>0&&<button type="button" disabled={inboxOpening||busy||taskBusy}
            onClick={()=>{
              setInboxCursor(inboxHistory[inboxHistory.length-1]);
              setInboxHistory(current=>current.slice(0,-1));
            }}>Предыдущая страница</button>}
          {inboxNext&&<button type="button" disabled={inboxOpening||busy||taskBusy}
            onClick={()=>{
              setInboxHistory(current=>[...current,inboxCursor]);
              setInboxCursor(inboxNext);
            }}>Следующая страница</button>}
        </div>}
        {inboxMessage&&<p role="alert">{inboxMessage}</p>}
      </div>
      <h2>Новое обращение</h2>
      <label>UUID подтверждённой связи <input aria-label="UUID подтверждённой связи"
        disabled={busy} value={link} onChange={e=>setLink(e.target.value)}/></label>
      <label>Источник <select aria-label="Источник" disabled={busy}
        value={source} onChange={e=>setSource(e.target.value as typeof source)}>
        <option value="WALK_IN">Личное обращение</option><option value="WEB">Сайт</option>
        <option value="PHONE">Звонок (только метка, без телефонии)</option>
      </select></label>
      <button type="button" disabled={busy||taskBusy||pendingLead!==null||pendingTask!==null||!uuid.test(link)}
        onClick={()=>void mutate(base,{customerLinkId:link,sourceCode:source})}>
        Создать обращение</button>
      <h2>Журнал обращений</h2>
      {rows.length===0?<p>{leadCursor?'На этой странице обращений нет.':'Обращений пока нет.'}</p>:<label>Обращение
        <select aria-label="Обращение" value={selected} disabled={taskBusy||busy||pendingLead!==null}
          onChange={e=>{setPendingTask(null);setSelected(e.target.value);}}>
          <option value="">Выберите обращение</option>
          {rows.map(x=><option value={x.leadId} key={x.leadId}>
            {leadSourceLabels[x.sourceCode]} · {leadStatusLabels[x.status]} · {x.leadId}</option>)}
        </select></label>}
      <div aria-label="Страницы журнала CRM">
        {leadHistory.length>0&&<button type="button"
          disabled={busy||taskBusy||pendingLead!==null||pendingTask!==null}
          onClick={()=>{setSelected('');setLeadNext(null);
            setLeadCursor(leadHistory[leadHistory.length-1]);
            setLeadHistory(h=>h.slice(0,-1));}}>Предыдущая страница обращений</button>}
        {leadNext&&<button type="button"
          disabled={busy||taskBusy||pendingLead!==null||pendingTask!==null}
          onClick={()=>{setSelected('');setLeadNext(null);
            setLeadHistory(h=>[...h,leadCursor]);setLeadCursor(leadNext);
          }}>Следующая страница обращений</button>}
      </div>
      {chosen&&<div>
        <p>Ответственный: {chosen.ownerId}. Источник: {leadSourceLabels[chosen.sourceCode]}.
          Текущий этап: {leadStatusLabels[chosen.status]}.</p>
        <StaffCrmContactPreferences key={scope+':'+selected} base={base} leadId={selected}
          onDenied={()=>{setRows([]);setSelected('');setInbox([]);setTasks([]);
            setStatus('denied');}}/>
        <ol>{chosen.events.map(e=><li key={e.id}>{leadStageLabels[e.stageCode]}
          {e.reasonCode?' · '+(leadReasonLabels[e.reasonCode]??'Неизвестная причина'):''}
          {e.appointmentId?' · запись '+e.appointmentId:''}</li>)}</ol>
        {!['BOOKED','LOST'].includes(chosen.status)&&<>
          <h3>Следующий этап</h3>
          <label>Этап <select aria-label="Этап" value={stage}
            onChange={e=>setStage(e.target.value as typeof stage)}>
            <option value="QUALIFIED">Обработано</option>
            <option value="BOOKED">Записан</option>
            <option value="LOST">Отказ</option>
          </select></label>
          {stage==='BOOKED'&&<label>UUID записи <input aria-label="UUID записи"
            value={appointment} onChange={e=>setAppointment(e.target.value)}/></label>}
          {stage==='LOST'&&<label>Причина отказа <select aria-label="Причина отказа"
            value={reason} onChange={e=>setReason(e.target.value)}>
            <option value="PRICE">Стоимость</option><option value="TIMING">Время</option>
            <option value="NO_RESPONSE">Нет ответа</option>
            <option value="OTHER">Другое</option>
          </select></label>}
          <button type="button" disabled={busy||taskBusy||pendingLead!==null||pendingTask!==null||
            (chosen.status==='NEW'&&stage==='BOOKED')||
            (chosen.status==='QUALIFIED'&&stage==='QUALIFIED')||
            (stage==='BOOKED'&&!uuid.test(appointment))}
            onClick={()=>void mutate(base+'/'+encodeURIComponent(selected)+'/stages',{
              stageCode:stage,reasonCode:stage==='LOST'?reason:null,
              appointmentId:stage==='BOOKED'?appointment:null,
            })}>Сохранить этап</button>
        </>}
      </div>}
      {chosen&&<div aria-label="Задачи по обращению">
        <h2>Задачи по обращению</h2>
        <p>Внутренние задачи для ответственного. Без звонков, отправки сообщений
          или автоматических напоминаний клиенту. Срок — локальное время браузера.</p>
        {taskStatus==='loading'&&<p role="status">Чтение задач…</p>}
        {taskStatus==='error'&&<p role="alert">Не удалось загрузить задачи. Обновите список.</p>}
        {taskStatus==='ready'&&<>
          <button type="button" disabled={taskBusy}
            onClick={()=>setTaskRevision(n=>n+1)}>Обновить задачи</button>
          <ol>{tasks.map(t=><li key={t.taskId}>
            {taskCodeLabels[t.taskCode]} · {new Date(t.dueAt).toLocaleString('ru-RU')} · {taskStatusLabels[t.status]}
            {t.originWorkOrderId&&<> · Наряд: {t.originWorkOrderId}</>}
            {t.status==='OPEN'&&<>
              <button type="button" disabled={taskBusy||busy||pendingLead!==null||pendingTask!==null}
                onClick={()=>void taskMutation(base+'/'+selected+'/tasks/'+t.taskId+'/events',{
                  action:'DONE',
                })}>Выполнено</button>
              <button type="button" disabled={taskBusy||busy||pendingLead!==null||pendingTask!==null}
                onClick={()=>void taskMutation(base+'/'+selected+'/tasks/'+t.taskId+'/events',{
                  action:'CANCELED',
                })}>Отменить задачу</button>
            </>}
          </li>)}</ol>
          {tasks.length===0&&<p>Задач пока нет.</p>}
          {chosen.status==='BOOKED'&&<>
            <h3>Контроль после выполненного ремонта</h3>
            <p>Создайте одну внутреннюю задачу на выполненный заказ-наряд.
              Сервер проверит связанный визит, завершённые работы и независимый QC PASS.
              Никаких сообщений клиенту не отправляется.</p>
            <label>UUID выполненного заказ-наряда <input
              aria-label="UUID выполненного заказ-наряда"
              disabled={taskBusy||busy||pendingTask!==null}
              value={reviewOrder} onChange={e=>setReviewOrder(e.target.value)}/></label>
            <label>Срок контроля после ремонта <input type="datetime-local"
              aria-label="Срок контроля после ремонта"
              disabled={taskBusy||busy||pendingTask!==null}
              value={reviewDue} onChange={e=>setReviewDue(e.target.value)}/></label>
            <button type="button"
              disabled={taskBusy||busy||pendingLead!==null||pendingTask!==null||
                !uuid.test(reviewOrder)||!reviewDue||
                !Number.isFinite(Date.parse(reviewDue))||
                Date.parse(reviewDue)<=Date.now()||
                Date.parse(reviewDue)>Date.now()+90*86400000}
              onClick={()=>void taskMutation(base+'/'+selected+'/service-review',{
                workOrderId:reviewOrder,dueAt:new Date(reviewDue).toISOString(),
              })}>Запланировать контроль после ремонта</button>
          </>}
          {!['BOOKED','LOST'].includes(chosen.status)&&<>
            <label>Тип задачи <select aria-label="Тип задачи" disabled={taskBusy}
              value={taskCode} onChange={e=>setTaskCode(e.target.value as Followup['taskCode'])}>
              <option value="CALL_BACK">Связаться позже (без автоотправки)</option>
              <option value="CHECK_BOOKING">Проверить запись</option>
              <option value="SERVICE_REVIEW">Проверить запрос на обслуживание</option>
            </select></label>
            <label>Срок задачи <input aria-label="Срок задачи" type="datetime-local"
              disabled={taskBusy} value={taskDue} onChange={e=>setTaskDue(e.target.value)}/></label>
            <button type="button" disabled={taskBusy||busy||pendingLead!==null||pendingTask!==null||!taskDue||
              !Number.isFinite(Date.parse(taskDue))||Date.parse(taskDue)<=Date.now()||
              Date.parse(taskDue)>Date.now()+90*86400000}
              onClick={()=>void taskMutation(base+'/'+selected+'/tasks',{
                taskCode,dueAt:new Date(taskDue).toISOString(),
              })}>Создать задачу</button>
          </>}
        </>}
        {taskMessage&&<p role="status">{taskMessage}</p>}
        {pendingTask&&pendingTask.scope===scope&&pendingTask.leadId===selected&&
          status==='ready'&&<button type="button" disabled={taskBusy}
            onClick={()=>void taskMutation(pendingTask.url,pendingTask.body,pendingTask)}>
            Повторить запрос с тем же ключом</button>}
      </div>}
      {message&&<p role="status">{message}</p>}
      {pendingLead&&pendingLead.scope===scope&&status==='ready'&&<button type="button"
        disabled={busy||taskBusy} onClick={()=>void mutate(pendingLead.url,pendingLead.body,pendingLead)}>
        Повторить операцию CRM с тем же ключом</button>}
    </>}
  </section>;
}
