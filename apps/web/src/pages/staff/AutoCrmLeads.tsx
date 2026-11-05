import React from 'react';

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
  status:'OPEN'|'DONE'|'CANCELED';event:FollowupEvent|null};
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
const validIso=(v:unknown):v is string=>typeof v==='string'&&
  !Number.isNaN(Date.parse(v));
function parseFollowup(v:unknown):Followup{
  if(!v||typeof v!=='object')throw Error('task');
  const t=v as Followup;
  if(![t.taskId,t.ownerId,t.creatorId].every(x=>typeof x==='string'&&uuid.test(x))||
    !['CALL_BACK','CHECK_BOOKING','SERVICE_REVIEW'].includes(t.taskCode)||
    !validIso(t.dueAt)||!validIso(t.createdAt)||
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
function parseList(v:unknown):Lead[]{
  if(!v||typeof v!=='object'||!('syntheticOnly' in v)||
    v.syntheticOnly!==true||!('items' in v)||
    !Array.isArray(v.items)||v.items.length>50)throw Error('shape');
  return v.items.map(parseLead);
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
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [message,setMessage]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const [busy,setBusy]=React.useState(false);
  const [tasks,setTasks]=React.useState<Followup[]>([]);
  const [taskStatus,setTaskStatus]=React.useState<'idle'|'loading'|'ready'|'error'>('idle');
  const [taskRevision,setTaskRevision]=React.useState(0);
  const [taskCode,setTaskCode]=React.useState<Followup['taskCode']>('CALL_BACK');
  const [taskDue,setTaskDue]=React.useState('');
  const [taskBusy,setTaskBusy]=React.useState(false);
  const [taskMessage,setTaskMessage]=React.useState('');
  const valid=uuid.test(org)&&uuid.test(loc);
  const scope=org+':'+loc;
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
        const res=await fetch(base,{credentials:'include',cache:'no-store',
          signal:controller.signal});
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('request');
        const parsed=parseList(await res.json());
        if(controller.signal.aborted)return;
        setRows(parsed);setLoadedScope(scope);setStatus('ready');
      }catch{if(!controller.signal.aborted)setStatus('error');}
    })();
    return ()=>controller.abort();
  },[org,loc,revision,valid]);
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
  async function taskMutation(url:string,body:object){
    if(taskBusy||taskStatus!=='ready'||loadedScope!==scope)return;
    setTaskBusy(true);setTaskMessage('');
    try{
      const csrf=await token();
      const response=await fetch(url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify({...body,requestKey:crypto.randomUUID()})});
      if([401,403,404].includes(response.status)){
        setTasks([]);setRows([]);setSelected('');setStatus('denied');return;
      }
      if(!response.ok){
        setTaskMessage(response.status===409?
          'Конфликт задачи. Обновите журнал, затем повторите действие.':
          'Нельзя сохранить задачу. Проверьте срок, этап и полномочия.');
        return;
      }
      parseFollowup(await response.json());
      setTaskMessage('Действие сохранено в неизменяемом журнале.');
      setTaskRevision(n=>n+1);
    }catch{
      setTasks([]);setTaskStatus('error');
      setTaskMessage('Результат неизвестен. Проверьте журнал до следующей попытки.');
    }finally{setTaskBusy(false);}
  }
  async function mutate(url:string,body:object){
    if(busy||status!=='ready'||loadedScope!==scope)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await token();
      const r=await fetch(url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify({...body,requestKey:crypto.randomUUID()})});
      if([401,403,404].includes(r.status)){
        setRows([]);setSelected('');setStatus('denied');return;
      }
      if(!r.ok){setMessage(r.status===409?
        'Конфликт или повторная запись. Обновите список перед новой операцией.':
        'Операция не выполнена. Проверьте связь и права.');return;}
      // POST returns the authoritative audit state. Update in-place to avoid
      // losing the selected lead while re-fetching its list.
      const updated=parseLead(await r.json());
      setRows(current=>{
        const rest=current.filter(x=>x.leadId!==updated.leadId);
        return [updated,...rest].slice(0,50);
      });
      setSelected(updated.leadId);
      setMessage('Изменение подтверждено. История обновлена.');
    }catch{
      setRows([]);setSelected('');setStatus('error');
      setMessage('Результат неизвестен; проверьте журнал до повтора.');
    }finally{setBusy(false);}
  }
  const chosen=rows.find(x=>x.leadId===selected);
  return <section aria-label="Синтетический реестр CRM (dev)">
    <h1>Обращения и этапы CRM</h1>
    <p>Только тестовый HTTPS-стенд. Один лид на подтверждённую связь,
      источник и филиал; никакой отправки сообщений, звонков или обработки
      реальных номеров. Этап «Записан» связывается с существующей записью,
      но не означает посещение или оплату.</p>
    <label>UUID организации <input aria-label="UUID организации" disabled={busy}
      value={org} onChange={e=>{setRows([]);setSelected('');setStatus('idle');setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" disabled={busy}
      value={loc} onChange={e=>{setRows([]);setSelected('');setStatus('idle');setLoc(e.target.value);}}/></label>
    {status==='idle'&&<p>Укажите организацию и филиал.</p>}
    {status==='loading'&&<p role="status">Чтение обращений…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<p role="alert">Не удалось загрузить обращения.</p>}
    {status==='ready'&&loadedScope===scope&&<>
      <button type="button" disabled={busy} onClick={()=>setRevision(n=>n+1)}>
        Обновить список</button>
      <h2>Новое обращение</h2>
      <label>UUID подтверждённой связи <input aria-label="UUID подтверждённой связи"
        disabled={busy} value={link} onChange={e=>setLink(e.target.value)}/></label>
      <label>Источник <select aria-label="Источник" disabled={busy}
        value={source} onChange={e=>setSource(e.target.value as typeof source)}>
        <option value="WALK_IN">Личное обращение</option><option value="WEB">Сайт</option>
        <option value="PHONE">Звонок (только метка, без телефонии)</option>
      </select></label>
      <button type="button" disabled={busy||!uuid.test(link)}
        onClick={()=>void mutate(base,{customerLinkId:link,sourceCode:source})}>
        Создать обращение</button>
      <h2>Журнал обращений</h2>
      {rows.length===0?<p>Обращений пока нет.</p>:<label>Обращение
        <select aria-label="Обращение" value={selected}
          onChange={e=>setSelected(e.target.value)}>
          <option value="">Выберите обращение</option>
          {rows.map(x=><option value={x.leadId} key={x.leadId}>
            {leadSourceLabels[x.sourceCode]} · {leadStatusLabels[x.status]} · {x.leadId}</option>)}
        </select></label>}
      {chosen&&<div>
        <p>Ответственный: {chosen.ownerId}. Источник: {leadSourceLabels[chosen.sourceCode]}.
          Текущий этап: {leadStatusLabels[chosen.status]}.</p>
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
          <button type="button" disabled={busy||
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
            {t.status==='OPEN'&&<>
              <button type="button" disabled={taskBusy}
                onClick={()=>void taskMutation(base+'/'+selected+'/tasks/'+t.taskId+'/events',{
                  action:'DONE',
                })}>Выполнено</button>
              <button type="button" disabled={taskBusy}
                onClick={()=>void taskMutation(base+'/'+selected+'/tasks/'+t.taskId+'/events',{
                  action:'CANCELED',
                })}>Отменить задачу</button>
            </>}
          </li>)}</ol>
          {tasks.length===0&&<p>Задач пока нет.</p>}
          {!['BOOKED','LOST'].includes(chosen.status)&&<>
            <label>Тип задачи <select aria-label="Тип задачи" disabled={taskBusy}
              value={taskCode} onChange={e=>setTaskCode(e.target.value as Followup['taskCode'])}>
              <option value="CALL_BACK">Связаться позже (без автоотправки)</option>
              <option value="CHECK_BOOKING">Проверить запись</option>
              <option value="SERVICE_REVIEW">Проверить запрос на обслуживание</option>
            </select></label>
            <label>Срок задачи <input aria-label="Срок задачи" type="datetime-local"
              disabled={taskBusy} value={taskDue} onChange={e=>setTaskDue(e.target.value)}/></label>
            <button type="button" disabled={taskBusy||!taskDue||
              !Number.isFinite(Date.parse(taskDue))||Date.parse(taskDue)<=Date.now()||
              Date.parse(taskDue)>Date.now()+90*86400000}
              onClick={()=>void taskMutation(base+'/'+selected+'/tasks',{
                taskCode,dueAt:new Date(taskDue).toISOString(),
              })}>Создать задачу</button>
          </>}
        </>}
        {taskMessage&&<p role="status">{taskMessage}</p>}
      </div>}
      {message&&<p role="status">{message}</p>}
    </>}
  </section>;
}
