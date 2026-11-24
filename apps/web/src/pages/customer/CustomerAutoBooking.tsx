import React from 'react';
import {BookingHistory} from '../shared/BookingHistory';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Resource={organizationId:string;locationId:string;customerLinkId:string;bayId:string;
  timeZone:string;vehicle:{make:string;model:string}};
type Booking={appointmentId:string;organizationId:string;locationId:string;customerLinkId:string;
  bookingId:string;bayId:string;startsAt:string;endsAt:string;status:'SCHEDULED'|'CANCELED';
  workOrderId:string;timeZone:string;canManage:boolean;serviceIds:string[]};
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
  const [nextResourceCursor,setNextResourceCursor]=React.useState<string|null>(null);
  const [bookings,setBookings]=React.useState<Booking[]>([]);
  const [nextCursor,setNextCursor]=React.useState<string|null>(null);
  const [state,setState]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [selected,setSelected]=React.useState('');
  const [day,setDay]=React.useState(today);
  const [slots,setSlots]=React.useState<Slot[]>([]);
  const [services,setServices]=React.useState<Service[]>([]);
  const [selectedServices,setSelectedServices]=React.useState<string[]>([]);
  const [serviceState,setServiceState]=React.useState('');
  const [slotState,setSlotState]=React.useState('');
  const [target,setTarget]=React.useState<Booking|null>(null);
  const [historyId,setHistoryId]=React.useState<string|null>(null);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const slotRequest=React.useRef(0);
  const resource=resources.find(r=>selected===r.customerLinkId+':'+r.bayId);
  const choices=target?resources.filter(r=>r.organizationId===target.organizationId&&
    r.locationId===target.locationId&&r.customerLinkId===target.customerLinkId):resources;
  const scoped=resource&&choices.some(r=>r===resource)?resource:null;
  const epoch=React.useRef(0);
  const listAbort=React.useRef<AbortController|null>(null);
  const [resourcePages,setResourcePages]=React.useState(1);
  const [bookingPages,setBookingPages]=React.useState(1);

  function invalidate(){
    epoch.current++;
    listAbort.current?.abort();
    slotRequest.current++;
  }
  function clearPrivate(){
    setResources([]);setBookings([]);setNextResourceCursor(null);setNextCursor(null);
    setSelected('');setTarget(null);setHistoryId(null);setSlots([]);setServices([]);setSelectedServices([]);
    setPending(null);setServiceState('');setSlotState('');
  }
  function deny(){
    invalidate();clearPrivate();setBusy(false);setMessage('');setState('denied');
  }
  function refreshLists(){
    invalidate();clearPrivate();setBusy(false);setState('loading');
    setResourcePages(1);setBookingPages(1);setRevision(x=>x+1);
  }
  React.useEffect(()=>()=>{invalidate();},[]);

  React.useEffect(()=>{
    const controller=new AbortController();
    listAbort.current?.abort();listAbort.current=controller;
    const generation=++epoch.current;
    slotRequest.current++;
    setState('loading');setResources([]);setBookings([]);setSlots([]);
    setServices([]);setSelectedServices([]);setTarget(null);setHistoryId(null);setPending(null);
    setServiceState('');setSlotState('');
    setNextResourceCursor(null);setNextCursor(null);
    const current=()=>!controller.signal.aborted&&generation===epoch.current;
    // Обе коллекции перечитываются с начала. Пустой 200 после отзыва
    // не оставляет ни старой записи, ни старого автомобиля/поста.
    async function readPages<T>(name:string,count:number,valid:(item:T)=>boolean,key:(item:T)=>string){
      const rows:T[]=[];const ids=new Set<string>(),cursors=new Set<string>();
      let cursor:string|null=null;
      for(let i=0;i<count;i++){
        const url='/api/v1/dev/auto/customer/booking/'+name+
          (cursor?'?'+new URLSearchParams({cursor}):'');
        const res=await fetch(url,{credentials:'include',cache:'no-store',signal:controller.signal});
        if(!current())throw Error('obsolete');
        if([401,403,404].includes(res.status)){deny();throw Error('denied');}
        if(!res.ok)throw Error('page');
        const raw:unknown=await res.json();
        if(!current())throw Error('obsolete');
        if(!raw||typeof raw!=='object'||!('syntheticOnly' in raw)||raw.syntheticOnly!==true||
          !('items' in raw)||!Array.isArray(raw.items)||raw.items.length>50||
          !('nextCursor' in raw)||!(raw.nextCursor===null||
            (typeof raw.nextCursor==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(raw.nextCursor)))||
          !raw.items.every(valid))throw Error('shape');
        cursor=raw.nextCursor as string|null;
        if(cursor!==null){
          if(cursors.has(cursor)||raw.items.length===0)throw Error('cursor');
          cursors.add(cursor);
        }
        for(const item of raw.items as T[]){
          const id=key(item);if(!ids.has(id)){ids.add(id);rows.push(item);}
        }
        if(cursor===null)break;
      }
      return {rows,cursor};
    }
    const resourceValid=(x:Resource)=>!!x&&uuid.test(x.organizationId)&&uuid.test(x.locationId)&&
      uuid.test(x.customerLinkId)&&uuid.test(x.bayId)&&typeof x.timeZone==='string'&&
      !!x.vehicle&&typeof x.vehicle.make==='string'&&typeof x.vehicle.model==='string';
    const bookingValid=(x:Booking)=>!!x&&uuid.test(x.appointmentId)&&uuid.test(x.organizationId)&&
      uuid.test(x.locationId)&&uuid.test(x.customerLinkId)&&uuid.test(x.bookingId)&&
      uuid.test(x.bayId)&&uuid.test(x.workOrderId)&&
      typeof x.startsAt==='string'&&typeof x.endsAt==='string'&&
      Date.parse(x.endsAt)>Date.parse(x.startsAt)&&
      (x.status==='CANCELED'||x.status==='SCHEDULED')&&typeof x.timeZone==='string'&&
      typeof x.canManage==='boolean'&&Array.isArray(x.serviceIds)&&x.serviceIds.length<=100&&
      x.serviceIds.every(id=>typeof id==='string'&&uuid.test(id));
    void Promise.all([
      readPages<Resource>('resources',resourcePages,resourceValid,
        x=>x.organizationId+':'+x.locationId+':'+x.customerLinkId+':'+x.bayId),
      readPages<Booking>('appointments',bookingPages,bookingValid,x=>x.appointmentId),
    ]).then(([r,b])=>{
      if(!current())return;
      setResources(r.rows);setBookings(b.rows);
      setNextResourceCursor(r.cursor);setNextCursor(b.cursor);
      setSelected(old=>r.rows.some(x=>old===x.customerLinkId+':'+x.bayId)?old:
        r.rows.length?r.rows[0].customerLinkId+':'+r.rows[0].bayId:'');
      setState('ready');
    }).catch(()=>{
      if(current()){invalidate();clearPrivate();setState('error');}
    });
    return ()=>controller.abort();
  },[revision,resourcePages,bookingPages]);

  function loadMoreResources(){
    if(nextResourceCursor&&!busy&&!pending&&state==='ready'){
      invalidate();setState('loading');setResourcePages(n=>n+1);
    }
  }
  function loadMore(){
    if(nextCursor&&!busy&&!pending&&state==='ready'){
      invalidate();setState('loading');setBookingPages(n=>n+1);
    }
  }

  React.useEffect(()=>{slotRequest.current++;setSlots([]);setSlotState('');},[selected,day,target,selectedServices]);

  React.useEffect(()=>{
    const controller=new AbortController();
    setServices([]);setSelectedServices([]);setServiceState('');
    if(state!=='ready'||!scoped||target)return ()=>controller.abort();
    const generation=epoch.current;
    const current=()=>!controller.signal.aborted&&generation===epoch.current;
    const query=new URLSearchParams({organizationId:scoped.organizationId,
      locationId:scoped.locationId,customerLinkId:scoped.customerLinkId});
    setServiceState('Загрузка услуг филиала…');
    void fetch('/api/v1/dev/auto/customer/booking/services?'+query,{
      credentials:'include',cache:'no-store',signal:controller.signal,
    }).then(async response=>{
      if(!current())return;
      if([401,403,404].includes(response.status)){deny();return;}
      if(!response.ok)throw Error('service catalog');
      const raw:unknown=await response.json();
      if(!raw||typeof raw!=='object'||!('syntheticOnly' in raw)||
         raw.syntheticOnly!==true||!('items' in raw)||!Array.isArray(raw.items)||
         raw.items.length>100||!raw.items.every((item:Service)=>uuid.test(item.id)&&
           typeof item.code==='string'&&typeof item.title==='string'&&
           Number.isInteger(item.durationMinutes)&&item.durationMinutes>=5&&
           item.durationMinutes<=1440))throw Error('shape');
      if(current()){setServices(raw.items as Service[]);setServiceState('');}
    }).catch(()=>{if(current())setServiceState('Не удалось получить каталог услуг.');});
    return ()=>controller.abort();
  },[state,scoped?.organizationId,scoped?.locationId,scoped?.customerLinkId,target]);

  async function search(){
    if(state!=='ready'||!scoped||busy||pending)return;
    const requestId=++slotRequest.current;
    setSlotState('Загрузка окон…');setSlots([]);
    const r=scoped;
    const query=new URLSearchParams({organizationId:r.organizationId,
      locationId:r.locationId,customerLinkId:r.customerLinkId,bayId:r.bayId,localDay:day});
    if(target)query.set('appointmentId',target.appointmentId);
    else selectedServices.forEach(id=>query.append('serviceId',id));
    try{
      const resp=await fetch('/api/v1/dev/auto/customer/booking/slots?'+query,{
        credentials:'include',cache:'no-store'});
      if(requestId!==slotRequest.current)return;
      if([401,403,404].includes(resp.status)){deny();return;}
      if(!resp.ok)throw Error('http');
      const raw:unknown=await resp.json();
      if(!raw||typeof raw!=='object'||!('items' in raw)||!Array.isArray(raw.items)||
        raw.items.length>50||!('guaranteed' in raw)||raw.guaranteed!==false||
        !('timeZone' in raw)||raw.timeZone!==r.timeZone||
        !raw.items.every((s:Slot)=>typeof s.startsAt==='string'&&
          typeof s.endsAt==='string'&&Date.parse(s.endsAt)>Date.parse(s.startsAt)))throw Error('shape');
      if(requestId!==slotRequest.current)return;
      setSlots(raw.items as Slot[]);
      const unconfigured=('openingHoursConfigured' in raw&&raw.openingHoursConfigured===false);
      setSlotState(unconfigured?
        (raw.items.length?'График филиала ещё не настроен; dev-кандидаты не ограничены часами работы.':
          'Свободных окон нет. График филиала ещё не настроен.') :
        raw.items.length?'Кандидаты не гарантируют бронь.':'Свободных окон нет.');
    }catch{if(requestId===slotRequest.current)setSlotState('Не удалось получить свободные окна.');}
  }

  async function mutate(path:string,body:string,key:string|null,known?:Pending){
    if(busy||state!=='ready'||(pending&&!known))return;
    const generation=epoch.current;
    const current=()=>generation===epoch.current;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if(!current())return;
      if([401,403,404].includes(csrf.status)){deny();return;}
      if(!csrf.ok)throw Error('csrf');
      const value:unknown=await csrf.json();
      if(!value||typeof value!=='object'||!('csrfToken' in value)||
        typeof value.csrfToken!=='string')throw Error('csrf');
      if(!current())return;
      const result=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken,
          ...(key?{'Idempotency-Key':key}:{})},body});
      if(!current())return;
      if([401,403,404].includes(result.status)){deny();return;}
      if(result.status===409||result.status===422){
        setPending(null);setMessage(result.status===409?
          'Окно занято либо прежний ключ уже недействителен. Обновите список.':
          'Неверное время или параметры записи.');refreshLists();return;
      }
      if(!result.ok)throw Error('uncertain result');
      setPending(null);setMessage('Изменение записи принято сервером.');
      refreshLists();
    }catch{
      if(!current())return;
      setPending({path,body,key});
      setMessage('Результат неизвестен. Повторите с прежним ключом или обновите список.');
    }finally{if(current())setBusy(false);}
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
      JSON.stringify({organizationId:booking.organizationId}),crypto.randomUUID());
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
          <button type="button" disabled={busy||!!pending} onClick={()=>setHistoryId(old=>old===b.appointmentId?null:b.appointmentId)}>История записи</button>
          {historyId===b.appointmentId&&<BookingHistory key={b.appointmentId} appointment={b.appointmentId}
            path={'/api/v1/dev/auto/customer/booking/appointments/'+b.appointmentId+'/history?'+
              new URLSearchParams({organizationId:b.organizationId,locationId:b.locationId})} onDenied={deny}/>}
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
      {nextCursor&&<button type="button" disabled={busy||!!pending}
        onClick={()=>void loadMore()}>Показать ещё записи</button>}
      <h4>{target?'Перенос записи':'Новая запись'}</h4>
      {target&&<button type="button" disabled={busy||!!pending} onClick={()=>{setTarget(null);setSelected('');}}>
        Отказаться от переноса</button>}
      {choices.length===0?<p>Нет доступных подтверждённых автомобилей и постов.</p>:<>
        <label>Автомобиль и пост <select aria-label="Автомобиль и пост" disabled={busy||!!pending}
          value={selected} onChange={e=>setSelected(e.target.value)}>
          {choices.map(r=><option key={r.customerLinkId+':'+r.bayId}
            value={r.customerLinkId+':'+r.bayId}>
            {r.vehicle.make} {r.vehicle.model} · {r.timeZone} · пост {r.bayId}
          </option>)}
        </select></label>
        {nextResourceCursor&&<button type="button" disabled={busy||!!pending}
          onClick={()=>void loadMoreResources()}>
          Показать ещё автомобили и посты
        </button>}
        {!target&&<fieldset aria-label="Услуги при записи (dev)"><legend>Работы по записи (необязательно)</legend>
          <p>Выберите не более 8 услуг и не более 24 часов суммарно. Поиск учитывает полную длительность. Первоначальный выбор работ не мешает переносу и отмене до начала оформления приёмщиком, осмотра, сметы или складских операций.</p>
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
        <label>День филиала <input aria-label="День филиала" type="date" disabled={busy||!!pending}
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
    {state!=='denied'&&message&&<p role="status">{message}</p>}
    {state==='ready'&&pending&&<div role="group" aria-label="Неопределённый результат записи">
      <button type="button" disabled={busy}
        onClick={()=>void mutate(pending.path,pending.body,pending.key,pending)}>
        Повторить прежний запрос</button>
      <button type="button" disabled={busy} onClick={()=>{
        refreshLists();
      }}>Перечитать записи</button>
    </div>}
    {state!=='loading'&&<button type="button" disabled={busy} onClick={refreshLists}>
      Обновить мои записи</button>}
  </section>;
}
