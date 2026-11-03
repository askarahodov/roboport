import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Resource={organizationId:string;locationId:string;customerLinkId:string;bayId:string;
  timeZone:string;vehicle:{make:string;model:string}};
type Booking={appointmentId:string;organizationId:string;locationId:string;customerLinkId:string;
  bookingId:string;bayId:string;startsAt:string;endsAt:string;status:'SCHEDULED'|'CANCELED';
  workOrderId:string;timeZone:string;canManage:boolean};
type Slot={startsAt:string;endsAt:string};
type Service={id:string;code:string;title:string;durationMinutes:number};
type Pending={path:string;body:string;key:string|null};
const today=()=>new Date().toISOString().slice(0,10);
const locale=(time:string,zone:string)=>new Intl.DateTimeFormat('ru-RU',{
  timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',
  hour:'2-digit',minute:'2-digit',hourCycle:'h23',
}).format(new Date(time));

export function CustomerAutoBooking(){
  const [resources,setResources]=React.useState<Resource[]>([]);
  const [bookings,setBookings]=React.useState<Booking[]>([]);
  const [state,setState]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [selected,setSelected]=React.useState('');
  const [day,setDay]=React.useState(today);
  const [slots,setSlots]=React.useState<Slot[]>([]);
  const [services,setServices]=React.useState<Service[]>([]);
  const [selectedServices,setSelectedServices]=React.useState<string[]>([]);
  const [serviceState,setServiceState]=React.useState('');
  const [slotState,setSlotState]=React.useState('');
  const [target,setTarget]=React.useState<Booking|null>(null);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const slotRequest=React.useRef(0);
  const resource=resources.find(r=>selected===r.customerLinkId+':'+r.bayId);
  const choices=target?resources.filter(r=>r.organizationId===target.organizationId&&
    r.locationId===target.locationId&&r.customerLinkId===target.customerLinkId):resources;
  const scoped=resource&&choices.some(r=>r===resource)?resource:null;
  React.useEffect(()=>{
    const controller=new AbortController();
    setState('loading');setResources([]);setBookings([]);setSlots([]);setTarget(null);
    void (async()=>{
      try{
        const [r,b]=await Promise.all([
          fetch('/api/v1/dev/auto/customer/booking/resources',{
            credentials:'include',cache:'no-store',signal:controller.signal}),
          fetch('/api/v1/dev/auto/customer/booking/appointments',{
            credentials:'include',cache:'no-store',signal:controller.signal}),
        ]);
        if(controller.signal.aborted)return;
        if([401,403,404].includes(r.status)||[401,403,404].includes(b.status)){
          setState('denied');return;
        }
        if(!r.ok||!b.ok)throw Error('request');
        const rr:unknown=await r.json(),bb:unknown=await b.json();
        if(!rr||typeof rr!=='object'||!('syntheticOnly' in rr)||rr.syntheticOnly!==true||
           !('items' in rr)||!Array.isArray(rr.items)||rr.items.length>50||
           !rr.items.every((x:Resource)=>uuid.test(x.organizationId)&&uuid.test(x.locationId)&&
             uuid.test(x.customerLinkId)&&uuid.test(x.bayId)&&typeof x.timeZone==='string'&&
             x.vehicle&&typeof x.vehicle.make==='string'&&typeof x.vehicle.model==='string')||
           !bb||typeof bb!=='object'||!('syntheticOnly' in bb)||bb.syntheticOnly!==true||
           !('items' in bb)||!Array.isArray(bb.items)||bb.items.length>50||
           !bb.items.every((x:Booking)=>uuid.test(x.appointmentId)&&uuid.test(x.organizationId)&&
             uuid.test(x.locationId)&&uuid.test(x.customerLinkId)&&
             uuid.test(x.bayId)&&uuid.test(x.workOrderId)&&
             typeof x.startsAt==='string'&&typeof x.endsAt==='string'&&
             (x.status==='CANCELED'||x.status==='SCHEDULED')&&
             typeof x.timeZone==='string'&&typeof x.canManage==='boolean'))throw Error('shape');
        if(controller.signal.aborted)return;
        const incoming=rr.items as Resource[];
        setResources(incoming);setBookings(bb.items as Booking[]);
        setSelected(old=>incoming.some(x=>old===x.customerLinkId+':'+x.bayId)?
          old:incoming.length?incoming[0].customerLinkId+':'+incoming[0].bayId:'');
        setState('ready');
      }catch{
        if(!controller.signal.aborted)setState('error');
      }
    })();
    return ()=>controller.abort();
  },[revision]);

  React.useEffect(()=>{slotRequest.current++;setSlots([]);setSlotState('');},[selected,day,target,selectedServices]);

  React.useEffect(()=>{
    const controller=new AbortController();
    setServices([]);setSelectedServices([]);setServiceState('');
    if(!scoped||target)return ()=>controller.abort();
    const query=new URLSearchParams({organizationId:scoped.organizationId,
      locationId:scoped.locationId,customerLinkId:scoped.customerLinkId});
    setServiceState('Загрузка услуг филиала…');
    void fetch('/api/v1/dev/auto/customer/booking/services?'+query,{
      credentials:'include',cache:'no-store',signal:controller.signal,
    }).then(async response=>{
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setState('denied');setServices([]);return;}
      if(!response.ok)throw Error('service catalog');
      const raw:unknown=await response.json();
      if(!raw||typeof raw!=='object'||!('syntheticOnly' in raw)||
         raw.syntheticOnly!==true||!('items' in raw)||!Array.isArray(raw.items)||
         raw.items.length>100||!raw.items.every((item:Service)=>uuid.test(item.id)&&
           typeof item.code==='string'&&typeof item.title==='string'&&
           Number.isInteger(item.durationMinutes)&&item.durationMinutes>=5&&
           item.durationMinutes<=1440))throw Error('shape');
      if(!controller.signal.aborted){setServices(raw.items as Service[]);setServiceState('');}
    }).catch(()=>{if(!controller.signal.aborted)setServiceState('Не удалось получить каталог услуг.');});
    return ()=>controller.abort();
  },[scoped?.organizationId,scoped?.locationId,scoped?.customerLinkId,target]);

  async function search(){
    if(!scoped||busy||pending)return;
    const requestId=++slotRequest.current;
    setSlotState('Загрузка окон…');setSlots([]);
    const r=scoped;
    const query=new URLSearchParams({organizationId:r.organizationId,
      locationId:r.locationId,customerLinkId:r.customerLinkId,bayId:r.bayId,localDay:day});
    if(!target)selectedServices.forEach(id=>query.append('serviceId',id));
    try{
      const resp=await fetch('/api/v1/dev/auto/customer/booking/slots?'+query,{
        credentials:'include',cache:'no-store'});
      if(requestId!==slotRequest.current)return;
      if([401,403,404].includes(resp.status)){setState('denied');setResources([]);setBookings([]);return;}
      if(!resp.ok)throw Error('http');
      const raw:unknown=await resp.json();
      if(!raw||typeof raw!=='object'||!('items' in raw)||!Array.isArray(raw.items)||
        raw.items.length>50||!('guaranteed' in raw)||raw.guaranteed!==false||
        !('timeZone' in raw)||raw.timeZone!==r.timeZone||
        !raw.items.every((s:Slot)=>typeof s.startsAt==='string'&&
          typeof s.endsAt==='string'&&Date.parse(s.endsAt)>Date.parse(s.startsAt)))throw Error('shape');
      if(requestId!==slotRequest.current)return;
      setSlots(raw.items as Slot[]);
      setSlotState(raw.items.length?'Кандидаты не гарантируют бронь.':'Свободных окон нет.');
    }catch{if(requestId===slotRequest.current)setSlotState('Не удалось получить свободные окна.');}
  }

  async function mutate(path:string,body:string,key:string|null,known?:Pending){
    if(busy||state!=='ready'||(pending&&!known))return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('csrf');
      const value:unknown=await csrf.json();
      if(!value||typeof value!=='object'||!('csrfToken' in value)||
        typeof value.csrfToken!=='string')throw Error('csrf');
      const result=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken,
          ...(key?{'Idempotency-Key':key}:{})},body});
      if([401,403,404].includes(result.status)){
        setResources([]);setBookings([]);setState('denied');setPending(null);return;
      }
      if(result.status===409||result.status===422){
        setPending(null);setMessage(result.status===409?
          'Окно занято либо прежний ключ уже недействителен. Обновите список.':
          'Неверное время или параметры записи.');setRevision(x=>x+1);return;
      }
      if(!result.ok)throw Error('uncertain result');
      setPending(null);setMessage('Изменение записи принято сервером.');
      setRevision(x=>x+1);
    }catch{
      setPending({path,body,key});
      setMessage('Результат неизвестен. Повторите с прежним ключом или обновите список.');
    }finally{setBusy(false);}
  }

  function selectSlot(slot:Slot){
    if(!scoped||pending)return;
    const path='/api/v1/dev/auto/customer/booking/appointments'+
      (target?'/'+encodeURIComponent(target.appointmentId)+'/reschedule':'');
    const payload=target?{organizationId:scoped.organizationId,bayId:scoped.bayId,
      startsAt:slot.startsAt,endsAt:slot.endsAt}:
      {organizationId:scoped.organizationId,locationId:scoped.locationId,
       customerLinkId:scoped.customerLinkId,bayId:scoped.bayId,
       startsAt:slot.startsAt,endsAt:slot.endsAt,serviceIds:selectedServices};
    void mutate(path,JSON.stringify(payload),crypto.randomUUID());
  }

  function cancel(booking:Booking){
    if(pending)return;
    void mutate('/api/v1/dev/auto/customer/booking/appointments/'+
      encodeURIComponent(booking.appointmentId)+'/cancel',
      JSON.stringify({organizationId:booking.organizationId}),null);
  }

  return <section aria-label="Самостоятельная запись автомобиля (dev)">
    <h3>Запись автомобиля на сервис</h3>
    <p>Только синтетический HTTPS-стенд. Выбирайте свой подтверждённый автомобиль,
      пост и свободное время. Кандидат — не гарантия брони. Настоящие ПДн не вводите.</p>
    {state==='loading'&&<p role="status">Загрузка собственных записей…</p>}
    {state==='denied'&&<p role="alert">Доступ к записи запрещён или отозван.</p>}
    {state==='error'&&<p role="alert">Не удалось загрузить записи.</p>}
    {state==='ready'&&<>
      <h4>Мои записи</h4>
      {bookings.length===0?<p>Записей пока нет.</p>:
        <ul>{bookings.map(b=><li key={b.appointmentId}>
          <strong>{locale(b.startsAt,b.timeZone)} — {locale(b.endsAt,b.timeZone)}
            {' '}({b.timeZone})</strong>
          <p>Заказ-наряд {b.workOrderId} · {b.status==='CANCELED'?'Отменена':'Активна'}</p>
          {b.canManage&&<div>
            <button type="button" disabled={busy||!!pending} onClick={()=>{
              setTarget(b);
              const candidate=resources.find(r=>r.organizationId===b.organizationId&&
                r.locationId===b.locationId&&r.customerLinkId===b.customerLinkId&&
                r.bayId===b.bayId);
              setSelected(candidate?candidate.customerLinkId+':'+candidate.bayId:'');
            }}>Перенести свою запись</button>
            <button type="button" disabled={busy||!!pending}
              onClick={()=>cancel(b)}>Отменить свою запись</button>
          </div>}
        </li>)}</ul>}
      <h4>{target?'Перенос записи':'Новая запись'}</h4>
      {target&&<button type="button" onClick={()=>{setTarget(null);setSelected('');}}>
        Отказаться от переноса</button>}
      {choices.length===0?<p>Нет доступных подтверждённых автомобилей и постов.</p>:<>
        <label>Автомобиль и пост <select aria-label="Автомобиль и пост"
          value={selected} onChange={e=>setSelected(e.target.value)}>
          {choices.map(r=><option key={r.customerLinkId+':'+r.bayId}
            value={r.customerLinkId+':'+r.bayId}>
            {r.vehicle.make} {r.vehicle.model} · {r.timeZone} · пост {r.bayId}
          </option>)}
        </select></label>
        {!target&&<fieldset aria-label="Услуги при записи (dev)"><legend>Работы по записи (необязательно)</legend>
          <p>Выберите не более 8 услуг и не более 24 часов суммарно. Поиск учитывает полную длительность, а после включения работ самостоятельный перенос и отмена недоступны.</p>
          {serviceState&&<p role="status">{serviceState}</p>}
          {services.map(service=><label key={service.id} style={{display:'block'}}>
            <input type="checkbox" aria-label={'Услуга '+service.code}
              checked={selectedServices.includes(service.id)}
              disabled={busy||!!pending||(!selectedServices.includes(service.id)&&(
                selectedServices.length>=8||services.filter(x=>selectedServices.includes(x.id))
                  .reduce((sum,x)=>sum+x.durationMinutes,0)+service.durationMinutes>1440))}
              onChange={e=>setSelectedServices(old=>e.target.checked?[...old,service.id]:
                old.filter(id=>id!==service.id))}/>
            {service.title} ({service.durationMinutes} мин.)
          </label>)}
        </fieldset>}
        <label>День филиала <input aria-label="День филиала" type="date"
          value={day} onChange={e=>setDay(e.target.value)}/></label>
        <button type="button" disabled={busy||!!pending||!scoped}
          onClick={()=>void search()}>Показать свободные окна</button>
        {slotState&&<p role="status">{slotState}</p>}
        {slots.length>0&&scoped&&<ul>{slots.map(slot=><li key={slot.startsAt}>
          <button type="button" disabled={busy||!!pending} onClick={()=>selectSlot(slot)}>
            {locale(slot.startsAt,scoped.timeZone)} — {locale(slot.endsAt,scoped.timeZone)}
            {' '}({scoped.timeZone})
          </button>
        </li>)}</ul>}
      </>}
    </>}
    {message&&<p role="status">{message}</p>}
    {pending&&<div role="group" aria-label="Неопределённый результат записи">
      <button type="button" disabled={busy}
        onClick={()=>void mutate(pending.path,pending.body,pending.key,pending)}>
        Повторить прежний запрос</button>
      <button type="button" disabled={busy} onClick={()=>{
        setPending(null);setRevision(x=>x+1);
      }}>Перечитать записи</button>
    </div>}
    {state!=='loading'&&<button type="button" onClick={()=>setRevision(x=>x+1)}>
      Обновить мои записи</button>}
  </section>;
}
