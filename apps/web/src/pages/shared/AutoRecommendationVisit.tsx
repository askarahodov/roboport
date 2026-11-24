import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Slot={startsAt:string;endsAt:string};
type Bay={id:string;code:string};
export type RepeatVisit=Slot&{appointmentId:string;workOrderId:string;bookingId:string;status:'SCHEDULED'|'CANCELED'};
export function validVisit(x:unknown):x is RepeatVisit{
  if(!x||typeof x!=='object')return false;
  const v=x as RepeatVisit;
  return [v.appointmentId,v.workOrderId,v.bookingId].every(id=>typeof id==='string'&&uuid.test(id))&&
    typeof v.startsAt==='string'&&typeof v.endsAt==='string'&&Date.parse(v.endsAt)>Date.parse(v.startsAt)&&
    (v.status==='SCHEDULED'||v.status==='CANCELED');
}
const validSlot=(v:Slot)=>v&&typeof v.startsAt==='string'&&typeof v.endsAt==='string'&&Date.parse(v.endsAt)>Date.parse(v.startsAt);
const fmt=(v:string,zone:string)=>new Intl.DateTimeFormat('ru-RU',{
  timeZone:zone,dateStyle:'medium',timeStyle:'short'}).format(new Date(v));

export function AutoRecommendationVisit({path,initialDay,onBooked,onDenied,onConflict,onClose}:{
  path:string;initialDay:string;onBooked:(visit:RepeatVisit)=>void;onDenied:()=>void;
  onConflict:()=>void;onClose:()=>void;
}){
  const [bays,setBays]=React.useState<Bay[]>([]);
  const [zone,setZone]=React.useState('');
  const [bay,setBay]=React.useState('');
  const [day,setDay]=React.useState(initialDay);
  const [slots,setSlots]=React.useState<Slot[]>([]);
  const [chosen,setChosen]=React.useState<Slot|null>(null);
  const [pending,setPending]=React.useState<string|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [ready,setReady]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const operation=React.useRef<AbortController|null>(null);
  async function run(fn:(signal:AbortSignal)=>Promise<void>,error:string){
    if(operation.current)return;
    const controller=new AbortController();operation.current=controller;setBusy(true);setMessage('');
    try{await fn(controller.signal);}catch{if(!controller.signal.aborted)setMessage(error);}
    finally{if(operation.current===controller){operation.current=null;if(!controller.signal.aborted)setBusy(false);}}
  }
  async function options(){
    await run(async signal=>{
      const response=await fetch(path,{credentials:'include',cache:'no-store',signal});
      if(signal.aborted)return;
      if([401,403,404].includes(response.status)){onDenied();return;}
      if(response.status===409){onConflict();return;}
      if(!response.ok)throw Error('options');
      const raw=await response.json();
      if(signal.aborted)return;
      if(raw.syntheticOnly!==true||typeof raw.timeZone!=='string'||
        !Array.isArray(raw.bays)||raw.bays.length>100||!raw.bays.every((b:Bay)=>
          b&&typeof b.id==='string'&&uuid.test(b.id)&&typeof b.code==='string')||
        new Set(raw.bays.map((b:Bay)=>b.id)).size!==raw.bays.length)throw Error('options');
      // Проверяем IANA часовой пояс до форматирования дат.
      const now=new Intl.DateTimeFormat('en-CA',{timeZone:raw.timeZone,
        year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
      const part=(name:string)=>now.find(x=>x.type===name)?.value??'';
      const localToday=part('year')+'-'+part('month')+'-'+part('day');
      setDay(old=>old<localToday?localToday:old);setZone(raw.timeZone);setBays(raw.bays);
      setBay(raw.bays[0]?.id??'');setReady(true);
    },'Не удалось получить посты. Повторите.');
  }
  React.useEffect(()=>{void options();return ()=>{operation.current?.abort();operation.current=null;};},[path]);
  async function search(){
    if(!ready||!bay||!day||pending)return;
    setSlots([]);setChosen(null);
    await run(async signal=>{
      const response=await fetch(path+'?'+new URLSearchParams({bayId:bay,localDay:day}),
        {credentials:'include',cache:'no-store',signal});
      if(signal.aborted)return;
      if([401,403,404].includes(response.status)){onDenied();return;}
      if(response.status===409){onConflict();return;}
      if(!response.ok)throw Error('slots');
      const raw=await response.json();
      if(signal.aborted)return;
      if(raw.syntheticOnly!==true||raw.timeZone!==zone||raw.guaranteed!==false||
        !Array.isArray(raw.items)||raw.items.length>50||!raw.items.every(validSlot))throw Error('slots');
      setSlots(raw.items);setMessage(raw.items.length?
        'Окна не гарантируют бронь до подтверждения сервером.':'Свободных окон на этот день нет.');
    },'Не удалось получить свободные окна.');
  }
  async function book(body:string){
    setPending(body);
    await run(async signal=>{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal});
      if(signal.aborted)return;
      if([401,403,404].includes(csrf.status)){onDenied();return;}
      if(!csrf.ok)throw Error('csrf');
      const token=await csrf.json();
      if(signal.aborted)return;
      if(typeof token.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},body});
      if(signal.aborted)return;
      if([401,403,404].includes(response.status)){onDenied();return;}
      if(response.status===409||response.status===422){setPending(null);onConflict();return;}
      if(!response.ok)throw Error('receipt');
      const raw=await response.json(),expected=JSON.parse(body);
      if(signal.aborted)return;
      if(raw.syntheticOnly!==true||!((response.status===201&&raw.replayed===false)||
        (response.status===200&&raw.replayed===true))||!validVisit(raw.visit)||
        raw.visit.status!=='SCHEDULED'||Date.parse(raw.visit.startsAt)!==Date.parse(expected.startsAt)||
        Date.parse(raw.visit.endsAt)!==Date.parse(expected.endsAt))throw Error('receipt');
      setPending(null);onBooked(raw.visit);
    },'Результат записи неизвестен. Повторите исходный запрос с тем же ключом.');
  }
  return <section aria-label="Запись по рекомендации">
    <h4>Повторный визит по рекомендации</h4>
    <p>Услуга и автомобиль берутся из исходного заказа. Выберите пост и время.</p>
    {!ready&&!busy&&<button type="button" onClick={()=>void options()}>Повторить загрузку постов</button>}
    {ready&&<>
      <label>Пост повторного визита <select value={bay} disabled={busy||pending!==null}
        onChange={e=>{setBay(e.target.value);setSlots([]);setChosen(null);}}>
        {bays.map(b=><option key={b.id} value={b.id}>{b.code}</option>)}</select></label>
      <label>Дата повторного визита <input type="date" value={day} disabled={busy||pending!==null}
        onChange={e=>{setDay(e.target.value);setSlots([]);setChosen(null);}}/></label>
      <button type="button" disabled={!bay||!day||busy||pending!==null}
        onClick={()=>void search()}>Найти время</button>
      {zone&&<p>Часовой пояс филиала: {zone}</p>}
      {slots.length>0&&<ul>{slots.map(slot=><li key={slot.startsAt}>
        <button type="button" disabled={busy||pending!==null} aria-pressed={chosen===slot}
          onClick={()=>setChosen(slot)}>{fmt(slot.startsAt,zone)} — {fmt(slot.endsAt,zone)}</button>
      </li>)}</ul>}
      {chosen&&!pending&&<div>
        <p>Подтвердите запись на {fmt(chosen.startsAt,zone)}.</p>
        <button type="button" disabled={busy} onClick={()=>void book(JSON.stringify({
          requestKey:crypto.randomUUID(),bayId:bay,...chosen}))}>Подтвердить повторный визит</button>
      </div>}
    </>}
    {busy&&<p role="status">Выполняется запрос…</p>}
    {message&&<p role="status">{message}</p>}
    {pending&&<button type="button" disabled={busy} onClick={()=>void book(pending)}>Повторить запись с тем же ключом</button>}
    <button type="button" disabled={busy} onClick={pending?onConflict:onClose}>
      {pending?'Перечитать план и сверить запись':'Закрыть выбор времени'}</button>
  </section>;
}
