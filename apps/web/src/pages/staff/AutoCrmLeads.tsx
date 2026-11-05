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
      setRevision(x=>x+1);
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
            {x.sourceCode} · {x.status} · {x.leadId}</option>)}
        </select></label>}
      {chosen&&<div>
        <p>Ответственный: {chosen.ownerId}. Источник: {chosen.sourceCode}.
          Текущий этап: {chosen.status}.</p>
        <ol>{chosen.events.map(e=><li key={e.id}>{e.stageCode}
          {e.reasonCode?' · '+e.reasonCode:''}
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
      {message&&<p role="status">{message}</p>}
    </>}
  </section>;
}
