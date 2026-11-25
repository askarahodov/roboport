import React from 'react';

const channels={CALL:'Звонок (имитация IP-телефонии)',EMAIL:'Почта',SMS:'SMS',MESSENGER:'Мессенджер',WALK_IN:'Личное обращение'};
const sources={WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение'};
const outcomes={REQUEST_RECEIVED:'Запрос получен',CALLBACK_REQUIRED:'Нужен обратный контакт',
  INFORMATION_REQUESTED:'Запрошена информация',DECLINED:'Клиент отказался'};
type Channel=keyof typeof channels;
type Outcome=keyof typeof outcomes;
type Item={interactionId:string;actorId:string;ownerId:string;sourceCode:'WEB'|'PHONE'|'WALK_IN';
  channelCode:Channel;outcomeCode:Outcome;direction:'INBOUND_SIM';createdAt:string;
  callbackTaskId:string|null;callbackDueAt:string|null};
type Pending={channelCode:Channel;outcomeCode:Outcome;requestKey:string;expectedActorId:string;callbackDueAt:string|null};
type Page={items:Item[];nextCursor:string|null;viewerId:string};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function item(raw:unknown):Item{
  if(!raw||typeof raw!=='object')throw Error('item');
  const x=raw as Item;
  if(![x.interactionId,x.actorId,x.ownerId].every(v=>typeof v==='string'&&uuid.test(v))||
    x.direction!=='INBOUND_SIM'||!['WEB','PHONE','WALK_IN'].includes(x.sourceCode)||
    !Object.hasOwn(channels,x.channelCode)||!Object.hasOwn(outcomes,x.outcomeCode)||
    typeof x.createdAt!=='string'||!Number.isFinite(Date.parse(x.createdAt)))throw Error('item');
  if((x.callbackTaskId===null)!==(x.callbackDueAt===null)||
    (x.callbackTaskId!==null&&(x.outcomeCode!=='CALLBACK_REQUIRED'||!uuid.test(x.callbackTaskId)||
      typeof x.callbackDueAt!=='string'||!Number.isFinite(Date.parse(x.callbackDueAt)))))throw Error('callback');
  return x;
}
function storageKey(endpoint:string,actor:string){return 'roboport.crm.interaction.v1:'+endpoint+':'+actor;}
function recover(endpoint:string,actor:string):Pending|null{
  const raw=sessionStorage.getItem(storageKey(endpoint,actor));
  if(raw===null)return null;
  const x=JSON.parse(raw) as Pending;
  if(!x||x.expectedActorId!==actor||!uuid.test(x.requestKey)||
    !Object.hasOwn(channels,x.channelCode)||!Object.hasOwn(outcomes,x.outcomeCode)||
    (x.callbackDueAt!==null&&(x.outcomeCode!=='CALLBACK_REQUIRED'||typeof x.callbackDueAt!=='string'||
      !Number.isFinite(Date.parse(x.callbackDueAt)))))throw Error('pending');
  return x;
}
function envelope(raw:unknown,leadId:string):Record<string,unknown>{
  if(!raw||typeof raw!=='object')throw Error('response');
  const x=raw as Record<string,unknown>;
  if(x.syntheticOnly!==true||x.sendAllowed!==false||x.leadId!==leadId||typeof x.viewerId!=='string'||!uuid.test(x.viewerId))throw Error('response');
  return x;
}
function parsePage(raw:unknown,leadId:string):Page{
  const x=envelope(raw,leadId);
  if(!Array.isArray(x.items)||x.items.length>25||
    !(x.nextCursor===null||(typeof x.nextCursor==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(x.nextCursor)&&x.items.length===25)))
    throw Error('page');
  const items=x.items.map(item);
  if(new Set(items.map(i=>i.interactionId)).size!==items.length)throw Error('duplicate');
  return {items,nextCursor:x.nextCursor as string|null,viewerId:x.viewerId as string};
}
export function CrmInteractions({base,leadId}:{base:string;leadId:string}){
  const [rows,setRows]=React.useState<Item[]>([]);
  const [next,setNext]=React.useState<string|null>(null);
  const [cursor,setCursor]=React.useState<string|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [channel,setChannel]=React.useState<Channel>('CALL');
  const [outcome,setOutcome]=React.useState<Outcome>('REQUEST_RECEIVED');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [viewer,setViewer]=React.useState('');
  const [due,setDue]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const active=React.useRef<AbortController|null>(null);
  const endpoint=base+'/'+encodeURIComponent(leadId)+'/interactions';
  React.useEffect(()=>()=>active.current?.abort(),[endpoint]);
  async function load(position:string|null=null){
    if(active.current)return;
    const control=new AbortController();active.current=control;
    setRows([]);setNext(null);setCursor(null);setViewer('');setPending(null);setStatus('loading');
    try{
      const r=await fetch(endpoint+(position?'?cursor='+encodeURIComponent(position):''),
        {credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(r.status)){setStatus('denied');return;}
      if(!r.ok)throw Error('http');
      const page=parsePage(await r.json(),leadId);
      if(control.signal.aborted)return;
      if(position&&position===page.nextCursor)throw Error('cursor');
      const restored=recover(endpoint,page.viewerId);
      setViewer(page.viewerId);setPending(restored);
      if(restored)setMessage('Есть незавершённый контакт. Повторите с тем же ключом.');
      setRows(page.items);setNext(page.nextCursor);setCursor(position);setStatus('ready');
    }catch{if(!control.signal.aborted)setStatus('error');}
    finally{if(active.current===control)active.current=null;}
  }
  function deny(){
    setRows([]);setNext(null);setCursor(null);setViewer('');setPending(null);setStatus('denied');
    setMessage('Доступ запрещён. Неизвестный исход можно повторить только с прежним ключом после восстановления доступа.');
  }
  async function submit(request:Pending,retry:boolean){
    if(active.current||status!=='ready'||request.expectedActorId!==viewer)return;
    const control=new AbortController();active.current=control;setBusy(true);setMessage('');
    let posted=false,refresh=false;
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(csrf.status)){if(!retry)setPending(null);deny();return;}
      if(!csrf.ok)throw Error('csrf');
      const c:unknown=await csrf.json();
      if(control.signal.aborted)return;
      if(!c||typeof c!=='object'||!('csrfToken' in c)||typeof c.csrfToken!=='string')throw Error('csrf');
      const previous=recover(endpoint,viewer);
      if(previous&&JSON.stringify(previous)!==JSON.stringify(request)){
        setPending(previous);setMessage('Есть незавершённый контакт. Повторите с тем же ключом.');return;
      }
      sessionStorage.setItem(storageKey(endpoint,viewer),JSON.stringify(request));
      setPending(request);posted=true;
      const r=await fetch(endpoint,{method:'POST',credentials:'include',cache:'no-store',signal:control.signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':c.csrfToken},body:JSON.stringify(request)});
      if(control.signal.aborted)return;
      if([401,403,404].includes(r.status)){if(!retry)sessionStorage.removeItem(storageKey(endpoint,viewer));deny();return;}
      if(r.status===409||r.status===422){sessionStorage.removeItem(storageKey(endpoint,viewer));setPending(null);setMessage('Запись отклонена. Сверьте журнал.');refresh=true;return;}
      if(!r.ok)throw Error('http');
      const result=envelope(await r.json(),leadId);
      if(control.signal.aborted)return;
      const saved=item(result.item);
      if((r.status!==201||result.replayed!==false)&&(r.status!==200||result.replayed!==true))throw Error('receipt');
      if(result.viewerId!==viewer||result.requestKey!==request.requestKey||saved.actorId!==viewer||
        saved.channelCode!==request.channelCode||saved.outcomeCode!==request.outcomeCode||
        (saved.callbackDueAt===null)!==(request.callbackDueAt===null)||
        (saved.callbackDueAt!==null&&Date.parse(saved.callbackDueAt)!==Date.parse(request.callbackDueAt!)))throw Error('receipt');
      sessionStorage.removeItem(storageKey(endpoint,viewer));
      setPending(null);setMessage('Входящий тестовый контакт записан. Отправки не было.');refresh=true;
    }catch{
      if(!control.signal.aborted){
        if(!posted&&!retry)setPending(null);
        setMessage(posted||retry?'Ответ неизвестен. Повторите с тем же ключом.':'Не удалось получить CSRF. Запрос не отправлен.');
      }
    }finally{
      if(active.current===control){active.current=null;if(!control.signal.aborted){setBusy(false);if(refresh)void load();}}
    }
  }
  return <section aria-label="Журнал тестовых контактов">
    <h3>Журнал входящих тестовых контактов</h3>
    <p>Только входящая синтетическая отметка, без номера, адреса, текста и отправки.
      При необходимости назначьте срок внутренней задачи обратного контакта; реальный звонок запрещён.</p>
    <button type="button" disabled={busy||status==='loading'} onClick={()=>void load()}>
      {status==='idle'?'Загрузить журнал контактов':'Обновить журнал контактов'}</button>
    {status==='loading'&&<p role="status">Чтение журнала…</p>}
    {status==='denied'&&<p role="alert">Доступ к журналу контактов запрещён. Данные очищены.</p>}
    {status==='error'&&<p role="alert">Журнал недоступен.</p>}
    {status==='ready'&&<>
      <label>Канал входящего контакта <select aria-label="Канал входящего контакта" value={channel}
        disabled={busy||pending!==null} onChange={e=>setChannel(e.target.value as Channel)}>
        {Object.entries(channels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label>Результат входящего контакта <select aria-label="Результат входящего контакта" value={outcome}
        disabled={busy||pending!==null} onChange={e=>setOutcome(e.target.value as Outcome)}>
        {Object.entries(outcomes).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      {outcome==='CALLBACK_REQUIRED'&&<label>Срок обратного контакта (необязательно)
        <input type="datetime-local" aria-label="Срок обратного контакта" value={due}
          disabled={busy||pending!==null} onChange={e=>setDue(e.target.value)}/></label>}
      <button type="button" disabled={busy||pending!==null||!viewer||
        (outcome==='CALLBACK_REQUIRED'&&due!==''&&(!Number.isFinite(Date.parse(due))||Date.parse(due)<=Date.now()))}
        onClick={()=>void submit({channelCode:channel,outcomeCode:outcome,requestKey:crypto.randomUUID(),
          expectedActorId:viewer,callbackDueAt:outcome==='CALLBACK_REQUIRED'&&due?new Date(due).toISOString():null},false)}>
        Записать входящий контакт</button>
      {pending&&<button type="button" disabled={busy} onClick={()=>void submit(pending,true)}>Повторить контакт с тем же ключом</button>}
      {rows.length===0?<p>Контактов нет.</p>:<ol>{rows.map(row=><li key={row.interactionId} style={{overflowWrap:'anywhere'}}>
        {channels[row.channelCode]} · {outcomes[row.outcomeCode]} · {new Date(row.createdAt).toLocaleString('ru-RU')}
        {row.callbackTaskId&&<p>Задача обратного контакта: {row.callbackTaskId} · срок {new Date(row.callbackDueAt!).toLocaleString('ru-RU')}. В очереди CRM.</p>}
        <details><summary>Аудит контакта</summary><dl><dt>Ответственный на момент контакта</dt><dd>{row.ownerId}</dd>
          <dt>Автор</dt><dd>{row.actorId}</dd><dt>Источник обращения</dt><dd>{sources[row.sourceCode]}</dd>
          <dt>UUID события</dt><dd>{row.interactionId}</dd></dl></details></li>)}</ol>}
      {cursor&&<button type="button" disabled={busy||pending!==null} onClick={()=>void load()}>Первая страница контактов</button>}
      {next&&<button type="button" disabled={busy||pending!==null} onClick={()=>void load(next)}>Следующая страница контактов</button>}
    </>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
