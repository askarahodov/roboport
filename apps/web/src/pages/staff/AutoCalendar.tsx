import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Entry = { appointmentId:string; bookingId:string; bayId:string;
  startsAt:string; endsAt:string; workOrderId:string; employeeId:string|null };
type Form = { bay:string; link:string; employee:string; starts:string; ends:string };
const empty:Form={bay:'',link:'',employee:'',starts:'',ends:''};
const validUtcDay=(day:string)=>{
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return false;
  const value=new Date(day+'T00:00:00Z');
  return !Number.isNaN(value.getTime())&&value.toISOString().slice(0,10)===day;
};

const validIsoInstant=(value:string)=>{
  const match=/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-](\d{2}):(\d{2}))$/i.exec(value);
  if(!match||!validUtcDay(match[1]))return false;
  const hour=Number(match[2]),minute=Number(match[3]),second=Number(match[4]);
  const offsetHour=match[7]===undefined?0:Number(match[7]);
  const offsetMinute=match[8]===undefined?0:Number(match[8]);
  return hour<24&&minute<60&&second<60&&offsetHour<=14&&offsetMinute<60&&
    (offsetHour!==14||offsetMinute===0)&&!Number.isNaN(Date.parse(value));
};

export function AutoCalendar() {
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [day,setDay]=React.useState(()=>new Date().toISOString().slice(0,10));
  const [items,setItems]=React.useState<Entry[]>([]);
  const [hasMore,setHasMore]=React.useState(false);
  const [cursor,setCursor]=React.useState<{startsAt:string;appointmentId:string}|null>(null);
  const [nextCursor,setNextCursor]=React.useState<{startsAt:string;appointmentId:string}|null>(null);
  const [history,setHistory]=React.useState<Array<{startsAt:string;appointmentId:string}|null>>([]);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [form,setForm]=React.useState<Form>(empty);
  const [busy,setBusy]=React.useState(false);
  const [slots,setSlots]=React.useState<Array<{startsAt:string;endsAt:string}>>([]);
  const [slotsMessage,setSlotsMessage]=React.useState('');
  const [slotsBusy,setSlotsBusy]=React.useState(false);
  const slotRequest=React.useRef(0);
  const [clearEmployee,setClearEmployee]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<{path:string;body:string;key:string;success:string;org:string;location:string}|null>(null);
  const [revision,setRevision]=React.useState(0);
  const [target,setTarget]=React.useState<string | null>(null);
  const valid=uuid.test(org.trim())&&uuid.test(location.trim())&&validUtcDay(day);
  React.useEffect(()=>{
    setItems([]);setHasMore(false);setNextCursor(null);setMessage('');setTarget(null);
    if(!valid){setStatus('idle');return;}
    const controller=new AbortController();
    setStatus('loading');
    const start=new Date(day+'T00:00:00Z');
    const end=new Date(start.getTime()+86400000);
    if(Number.isNaN(start.getTime())){setStatus('idle');return;}
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/calendar';
    const query=new URLSearchParams({locationId:location.trim(),startsAt:start.toISOString(),endsAt:end.toISOString()});
    if(cursor){query.set('afterStartsAt',cursor.startsAt);query.set('afterAppointmentId',cursor.appointmentId);}
    void fetch(path+'?'+query,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async response=>{
        if(controller.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw new Error();
        const data:unknown=await response.json();
        if(!data||typeof data!=='object'||!('items' in data)||!Array.isArray(data.items)||
          !data.items.every((x:unknown)=>x&&typeof x==='object' &&
            ['appointmentId','bookingId','bayId','workOrderId','startsAt','endsAt'].every(
              key=>key in x&&typeof (x as Record<string,unknown>)[key]==='string')))throw new Error();
        if(!data.items.every((x:Entry)=>'employeeId' in x &&
          (x.employeeId===null||(typeof x.employeeId==='string'&&uuid.test(x.employeeId)))))throw new Error();
        if(!('hasMore' in data)||typeof data.hasMore!=='boolean')throw new Error();
        const next=data.hasMore && 'nextStartsAt' in data && typeof data.nextStartsAt==='string' &&
          'nextAppointmentId' in data && typeof data.nextAppointmentId==='string' &&
          uuid.test(data.nextAppointmentId)?{startsAt:data.nextStartsAt,appointmentId:data.nextAppointmentId}:null;
        if(data.hasMore&&!next)throw new Error();
        if(!controller.signal.aborted){setItems(data.items as Entry[]);setHasMore(data.hasMore);setNextCursor(next);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[org,location,day,revision,valid,cursor]);
  function resetSlots(){slotRequest.current++;setSlots([]);setSlotsMessage('');setSlotsBusy(false);}
  function resetPage(){setCursor(null);setHistory([]);resetSlots();}
  React.useEffect(()=>{setPending(null);},[org,location]);
  async function findSlots(){
    const retained=target?items.find(x=>x.appointmentId===target)?.employeeId:null;
    const effectiveEmployee=target&&clearEmployee?null:(form.employee.trim()||retained||null);
    if(!valid||!uuid.test(form.bay.trim())||
       (effectiveEmployee!==null&&!uuid.test(effectiveEmployee)))return;
    const requestId=++slotRequest.current;
    setSlotsBusy(true);setSlots([]);setSlotsMessage('');
    const query=new URLSearchParams({locationId:location.trim(),bayId:form.bay.trim(),day});
    if(effectiveEmployee)query.set('employeeId',effectiveEmployee);
    if(target&&!target.startsWith('cancel:'))query.set('appointmentId',target);
    try{
      const response=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
        '/slot-candidates?'+query,{credentials:'include',cache:'no-store'});
      if([401,403,404].includes(response.status)){
        if(requestId===slotRequest.current)setSlotsMessage('Нет доступа к окнам записи.');
        return;
      }
      if(!response.ok)throw new Error();
      const data:unknown=await response.json();
      if(!data||typeof data!=='object'||!('guaranteed' in data)||data.guaranteed!==false||
        !('items' in data)||!Array.isArray(data.items)||data.items.length>48||
        !data.items.every((x:unknown)=>x&&typeof x==='object'&&
          'startsAt' in x&&typeof x.startsAt==='string'&&validIsoInstant(x.startsAt)&&
          'endsAt' in x&&typeof x.endsAt==='string'&&validIsoInstant(x.endsAt)&&
          Date.parse(x.endsAt)>Date.parse(x.startsAt)))throw new Error();
      if(requestId!==slotRequest.current)return;
      setSlots(data.items as Array<{startsAt:string;endsAt:string}>);
      if(data.items.length===0)setSlotsMessage('Подходящих окон нет.');
    }catch{if(requestId===slotRequest.current)setSlotsMessage('Не удалось получить окна записи.');}
    finally{if(requestId===slotRequest.current)setSlotsBusy(false);}
  }

  async function action(path:string,payload:unknown,success:string,
                        retry?:{path:string;body:string;key:string;success:string;org:string;location:string}){
    if(busy||!valid||(pending&&!retry))return;
    const isIdempotent=path.endsWith('/reschedule')||path.endsWith('/appointments');
    const snapshot=retry??(isIdempotent?{
      path,body:JSON.stringify(payload),key:crypto.randomUUID(),success,
      org:org.trim(),location:location.trim(),
    }:null);
    if(retry&&(retry.org!==org.trim()||retry.location!==location.trim()))return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw new Error('csrf');
      const data:unknown=await csrf.json();
      if(!data||typeof data!=='object'||!('csrfToken' in data)||typeof data.csrfToken!=='string')throw new Error('csrf');
      const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrfToken,
          ...(snapshot?{'Idempotency-Key':snapshot.key}:{})},
        body:snapshot?snapshot.body:JSON.stringify(payload)});
      if(!response.ok){setPending(null);setMessage(response.status===409?'Пост или специалист занят / ключ запроса использован (409).':
        [401,403,404].includes(response.status)?'Доступ запрещён.':'Ошибка операции ('+response.status+').');return;}
      setPending(null);setMessage(success);setTarget(null);setClearEmployee(false);setForm(empty);resetPage();setRevision(n=>n+1);
    }catch{
      if(snapshot){setPending(snapshot);setMessage('Результат запроса неизвестен. Повторите с прежним ключом или обновите календарь перед новой операцией.');}
      else setMessage('Не удалось выполнить запрос. Проверьте сессию.');
    }finally{setBusy(false);}
  }
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/appointments';
  function fields(includeLink:boolean){
    return <div>
      <label>UUID поста <input aria-label="UUID поста" value={form.bay}
        onChange={e=>{resetSlots();setForm(s=>({...s,bay:e.target.value}));}}/></label>
      {includeLink&&<label>UUID связи клиента с автомобилем <input aria-label="UUID связи клиента"
        value={form.link} onChange={e=>setForm(s=>({...s,link:e.target.value}))}/></label>}
      <label>UUID специалиста (необязательно) <input aria-label="UUID специалиста" disabled={clearEmployee} value={form.employee}
        onChange={e=>{resetSlots();setForm(s=>({...s,employee:e.target.value}));}}/></label>
      {target&&<label><input type="checkbox" aria-label="Снять назначение специалиста" checked={clearEmployee}
        onChange={e=>{resetSlots();setClearEmployee(e.target.checked);}}/> Снять назначение специалиста</label>}
      <button type="button" disabled={slotsBusy||!valid||!uuid.test(form.bay.trim())||
        (!(target&&clearEmployee)&&!!form.employee.trim()&&!uuid.test(form.employee.trim()))}
        onClick={()=>void findSlots()}>Проверить свободные окна (dev)</button>
      {slotsMessage&&<p role="status">{slotsMessage}</p>}
      {slots.length>0&&<div aria-label="Кандидаты времени"><p>Кандидаты, не гарантия брони:</p><ul>
        {slots.map(slot=><li key={slot.startsAt}><button type="button" onClick={()=>{setForm(v=>({...v,starts:slot.startsAt,ends:slot.endsAt}));resetSlots();}}>
          {slot.startsAt} — {slot.endsAt}</button></li>)}
      </ul></div>}
      <label>Начало (ISO 8601 с часовым поясом) <input aria-label="Начало ISO" value={form.starts}
        onChange={e=>setForm(s=>({...s,starts:e.target.value}))}
        placeholder="2030-01-01T10:00:00+03:00"/></label>
      <label>Конец (ISO 8601 с часовым поясом) <input aria-label="Конец ISO" value={form.ends}
        onChange={e=>setForm(s=>({...s,ends:e.target.value}))}/></label>
    </div>;
  }
  const validForm=(clearEmployee||!form.employee.trim()||uuid.test(form.employee.trim()))&&uuid.test(form.bay.trim())&&(!target?uuid.test(form.link.trim()):true)&&
    validIsoInstant(form.starts)&&validIsoInstant(form.ends)&&
    Date.parse(form.ends)>Date.parse(form.starts)&&
    Date.parse(form.ends)-Date.parse(form.starts)<=86400000&&
    Date.parse(form.starts)>Date.now();
  return <section aria-label="Календарь приёмщика (dev)" style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Календарь приёмщика</h1>
    <p>Синтетический HTTPS-стенд. Время поиска — сутки UTC. Реальных клиентов не добавляйте.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org} onChange={e=>{resetPage();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={location} onChange={e=>{resetPage();setLocation(e.target.value);}}/></label>
    <label>День (UTC) <input aria-label="День UTC" type="date" value={day} onChange={e=>{resetPage();setDay(e.target.value);}}/></label>
    {status==='idle'&&<p>Укажите организацию, филиал и день.</p>}
    {status==='loading'&&<p role="status">Загрузка календаря…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или dev API отключён.</p>}
    {status==='error'&&<p role="alert">Ошибка загрузки календаря. <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить</button></p>}
    {status==='ready'&&<><h2>Записи</h2>
      {hasMore&&<p role="status">Есть дополнительные записи. Перейдите на следующую страницу.</p>}
      {items.length===0?<p>Записей нет.</p>:<ul>{items.map(entry=><li key={entry.appointmentId}>
        <strong>{entry.startsAt} — {entry.endsAt}</strong> · пост {entry.bayId}
        <p>Appointment {entry.appointmentId} · WorkOrder {entry.workOrderId}</p>
        <p>Специалист: {entry.employeeId??'не назначен'}</p>
        <button type="button" disabled={busy} onClick={()=>{resetSlots();setTarget(entry.appointmentId);setClearEmployee(false);setForm({...empty,bay:entry.bayId,employee:entry.employeeId??''});}}>
          Перенести</button>
        <button type="button" disabled={busy} onClick={()=>{resetSlots();setTarget('cancel:'+entry.appointmentId);}}>Отменить запись</button>
      </li>)}</ul>}
      <nav aria-label="Страницы календаря">
        <span>Страница {history.length+1}</span>
        {history.length>0&&<button type="button" disabled={busy} onClick={()=>{
          resetSlots();setCursor(history[history.length-1]);setHistory(s=>s.slice(0,-1));
        }}>Предыдущая страница</button>}
        {hasMore&&nextCursor&&<button type="button" disabled={busy} onClick={()=>{
          resetSlots();setHistory(s=>[...s,cursor]);setCursor(nextCursor);
        }}>Следующая страница</button>}
      </nav>
      {target?.startsWith('cancel:')&&<div role="group" aria-label="Подтверждение отмены">
        <p>Отменить выбранную запись?</p>
        <button type="button" disabled={busy} onClick={()=>void action(base+'/'+target.slice(7)+'/cancel',{},'Запись отменена.')}>Подтвердить отмену</button>
        <button type="button" onClick={()=>{resetSlots();setTarget(null);setClearEmployee(false);}}>Назад</button>
      </div>}
      {!target?.startsWith('cancel:')&&<form onSubmit={e=>{
        e.preventDefault();
        if(!validForm||pending)return;
        const payload={bayId:form.bay.trim(),startsAt:form.starts,endsAt:form.ends,
          ...(target&&clearEmployee?{employeeId:null}:form.employee.trim()?{employeeId:form.employee.trim()}:{})};
        if(target)void action(base+'/'+target+'/reschedule',payload,'Запись перенесена.');
        else void action(base,{...payload,locationId:location.trim(),customerLinkId:form.link.trim()},'Запись создана.');
      }}>
        <h2>{target?'Перенести запись':'Новая запись'}</h2>{fields(!target)}
        <button type="submit" disabled={busy||!validForm}>{target?'Сохранить перенос':'Создать запись'}</button>
        {target&&<button type="button" onClick={()=>{resetSlots();setTarget(null);setClearEmployee(false);setForm(empty);}}>Отмена переноса</button>}
      </form>}
    </>}
    {message&&<p role="status">{message}</p>}
    {pending&&<div role="group" aria-label="Неопределённый результат записи">
      <button type="button" disabled={busy} onClick={()=>void action(pending.path,JSON.parse(pending.body),pending.success,pending)}>Повторить запрос с тем же ключом</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setMessage('Результат неизвестен: проверьте календарь перед новой записью.');setRevision(n=>n+1);}}>Закрыть повтор и проверить календарь</button>
    </div>}
  </section>;
}
