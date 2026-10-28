import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Entry = { appointmentId:string; bookingId:string; bayId:string;
  startsAt:string; endsAt:string; workOrderId:string };
type Form = { bay:string; link:string; starts:string; ends:string };
const empty:Form={bay:'',link:'',starts:'',ends:''};

export function AutoCalendar() {
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [day,setDay]=React.useState('2030-01-01');
  const [items,setItems]=React.useState<Entry[]>([]);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [form,setForm]=React.useState<Form>(empty);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const [target,setTarget]=React.useState<string | null>(null);
  const valid=uuid.test(org.trim())&&uuid.test(location.trim())&&/^\d{4}-\d{2}-\d{2}$/.test(day);
  React.useEffect(()=>{
    setItems([]);setMessage('');setTarget(null);
    if(!valid){setStatus('idle');return;}
    const controller=new AbortController();
    setStatus('loading');
    const start=new Date(day+'T00:00:00Z');
    const end=new Date(start.getTime()+86400000);
    if(Number.isNaN(start.getTime())){setStatus('idle');return;}
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/calendar';
    const query=new URLSearchParams({locationId:location.trim(),startsAt:start.toISOString(),endsAt:end.toISOString()});
    void fetch(path+'?'+query,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async response=>{
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw new Error();
        const data:unknown=await response.json();
        if(!data||typeof data!=='object'||!('items' in data)||!Array.isArray(data.items)||
          !data.items.every((x:unknown)=>x&&typeof x==='object' &&
            ['appointmentId','bookingId','bayId','workOrderId','startsAt','endsAt'].every(
              key=>key in x&&typeof (x as Record<string,unknown>)[key]==='string')))throw new Error();
        if(!controller.signal.aborted){setItems(data.items as Entry[]);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[org,location,day,revision,valid]);

  async function action(path:string,payload:unknown,success:string){
    if(busy||!valid)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw new Error();
      const data:unknown=await csrf.json();
      if(!data||typeof data!=='object'||!('csrfToken' in data)||typeof data.csrfToken!=='string')throw new Error();
      const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrfToken},
        body:JSON.stringify(payload)});
      if(!response.ok){setMessage(response.status===409?'Пост занят (409).':
        [401,403,404].includes(response.status)?'Доступ запрещён.':'Ошибка операции ('+response.status+').');return;}
      setMessage(success);setTarget(null);setRevision(n=>n+1);
    }catch{setMessage('Не удалось выполнить запрос. Проверьте сессию.');}
    finally{setBusy(false);}
  }
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/appointments';
  function fields(includeLink:boolean){
    return <div>
      <label>UUID поста <input aria-label="UUID поста" value={form.bay}
        onChange={e=>setForm(s=>({...s,bay:e.target.value}))}/></label>
      {includeLink&&<label>UUID связи клиента с автомобилем <input aria-label="UUID связи клиента"
        value={form.link} onChange={e=>setForm(s=>({...s,link:e.target.value}))}/></label>}
      <label>Начало (ISO 8601 с часовым поясом) <input aria-label="Начало ISO" value={form.starts}
        onChange={e=>setForm(s=>({...s,starts:e.target.value}))}
        placeholder="2030-01-01T10:00:00+03:00"/></label>
      <label>Конец (ISO 8601 с часовым поясом) <input aria-label="Конец ISO" value={form.ends}
        onChange={e=>setForm(s=>({...s,ends:e.target.value}))}/></label>
    </div>;
  }
  const validForm=uuid.test(form.bay.trim())&&(!target?uuid.test(form.link.trim()):true)&&
    !Number.isNaN(Date.parse(form.starts))&&!Number.isNaN(Date.parse(form.ends)) &&
    /(?:Z|[+-]\d{2}:\d{2})$/i.test(form.starts)&&/(?:Z|[+-]\d{2}:\d{2})$/i.test(form.ends)&&
    Date.parse(form.ends)>Date.parse(form.starts);
  return <section aria-label="Календарь приёмщика (dev)" style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Календарь приёмщика</h1>
    <p>Синтетический HTTPS-стенд. Время поиска — сутки UTC. Реальных клиентов не добавляйте.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={location} onChange={e=>setLocation(e.target.value)}/></label>
    <label>День (UTC) <input aria-label="День UTC" type="date" value={day} onChange={e=>setDay(e.target.value)}/></label>
    {status==='idle'&&<p>Укажите организацию, филиал и день.</p>}
    {status==='loading'&&<p role="status">Загрузка календаря…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или dev API отключён.</p>}
    {status==='error'&&<p role="alert">Ошибка загрузки календаря. <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить</button></p>}
    {status==='ready'&&<><h2>Записи</h2>
      {items.length===0?<p>Записей нет.</p>:<ul>{items.map(entry=><li key={entry.appointmentId}>
        <strong>{entry.startsAt} — {entry.endsAt}</strong> · пост {entry.bayId}
        <p>Appointment {entry.appointmentId} · WorkOrder {entry.workOrderId}</p>
        <button type="button" disabled={busy} onClick={()=>{setTarget(entry.appointmentId);setForm({...empty,bay:entry.bayId});}}>
          Перенести</button>
        <button type="button" disabled={busy} onClick={()=>setTarget('cancel:'+entry.appointmentId)}>Отменить запись</button>
      </li>)}</ul>}
      {target?.startsWith('cancel:')&&<div role="group" aria-label="Подтверждение отмены">
        <p>Отменить выбранную запись?</p>
        <button type="button" disabled={busy} onClick={()=>void action(base+'/'+target.slice(7)+'/cancel',{},'Запись отменена.')}>Подтвердить отмену</button>
        <button type="button" onClick={()=>setTarget(null)}>Назад</button>
      </div>}
      {!target?.startsWith('cancel:')&&<form onSubmit={e=>{
        e.preventDefault();
        if(!validForm)return;
        const payload={bayId:form.bay.trim(),startsAt:form.starts,endsAt:form.ends};
        if(target)void action(base+'/'+target+'/reschedule',payload,'Запись перенесена.');
        else void action(base,{...payload,locationId:location.trim(),customerLinkId:form.link.trim()},'Запись создана.');
      }}>
        <h2>{target?'Перенести запись':'Новая запись'}</h2>{fields(!target)}
        <button type="submit" disabled={busy||!validForm}>{target?'Сохранить перенос':'Создать запись'}</button>
        {target&&<button type="button" onClick={()=>setTarget(null)}>Отмена переноса</button>}
      </form>}
    </>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
