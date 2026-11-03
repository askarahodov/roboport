import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Service={id:string;code:string;title:string;durationMinutes:number};
type StockItem={skuId:string;article:string;available:number};
type BookingMaterial={skuId:string;kind:'PART'|'CONSUMABLE';quantity:number};
type OpeningPolicy={configured:boolean;version:number;timeZone:string;
  windows:Array<{weekday:number;opensMinute:number;closesMinute:number}>;
  closedDates:string[];syntheticOnly:true};
type OpeningDraft={weekday:number;open:string;close:string};
const weekdays=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
const toClock=(minutes:number)=>String(Math.floor(minutes/60)).padStart(2,'0')+':'+
  String(minutes%60).padStart(2,'0');
const fromClock=(value:string)=>{
  if(!/^(?:[01]\d|2[0-4]):[0-5]\d$/.test(value))return NaN;
  const [h,m]=value.split(':').map(Number);
  return h===24&&m!==0?NaN:h*60+m;
};
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

/** Ответы dev API не раскрывают клиентские данные; показываем только известные коды. */
function bookingFailure(status:number, code:unknown):string {
  if(status===403||status===401||status===404)return 'Доступ запрещён. Проверьте сессию и права филиала.';
  if(status===409){
    switch(code){
      case 'STOCK_RECONCILIATION_REQUIRED':
        return 'Отмена невозможна: сначала выполните сверку склада и возврат/освобождение резерва по заказ-наряду (409).';
      case 'STOCK_UNAVAILABLE':
        return 'Недостаточно свободного остатка для резерва. Обновите остатки и проверьте запись перед повтором (409).';
      case 'LOCATION_CLOSED':
        return 'Филиал закрыт в выбранное время. Проверьте график и выберите другое окно (409).';
      case 'IDEMPOTENCY_KEY_REUSED':
        return 'Ключ повтора уже использован для другого запроса. Обновите календарь перед новой операцией (409).';
      case 'RESOURCE_BUSY':
        return 'Пост или специалист уже занят. Обновите свободные окна (409).';
      default:
        return 'Конфликт при выполнении операции. Обновите календарь перед повтором (409).';
    }
  }
  if(status===422){
    return code==='SERVICES_EXCEED_SLOT'
      ? 'Выбранные работы не помещаются в интервал записи. Выберите более длинное окно (422).'
      : 'Проверьте данные записи и время с часовым поясом (422).';
  }
  return 'Ошибка операции ('+status+').';
}

export function AutoCalendar() {
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [day,setDay]=React.useState(()=>new Date().toISOString().slice(0,10));
  const [timeZone,setTimeZone]=React.useState('UTC');
  const [items,setItems]=React.useState<Entry[]>([]);
  const [hasMore,setHasMore]=React.useState(false);
  const [cursor,setCursor]=React.useState<{startsAt:string;appointmentId:string}|null>(null);
  const [nextCursor,setNextCursor]=React.useState<{startsAt:string;appointmentId:string}|null>(null);
  const [history,setHistory]=React.useState<Array<{startsAt:string;appointmentId:string}|null>>([]);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [form,setForm]=React.useState<Form>(empty);
  const [services,setServices]=React.useState<Service[]>([]);
  const [selectedServices,setSelectedServices]=React.useState<string[]>([]);
  const [serviceMessage,setServiceMessage]=React.useState('');
  const [stock,setStock]=React.useState<StockItem[]>([]);
  const [materials,setMaterials]=React.useState<BookingMaterial[]>([]);
  const [stockMessage,setStockMessage]=React.useState('');
  const [opening,setOpening]=React.useState<OpeningPolicy|null>(null);
  const [openingState,setOpeningState]=React.useState('График загружается…');
  const [openingWindows,setOpeningWindows]=React.useState<OpeningDraft[]>([]);
  const [closedText,setClosedText]=React.useState('');
  const [openingBusy,setOpeningBusy]=React.useState(false);
  const [openingRevision,setOpeningRevision]=React.useState(0);
  const [busy,setBusy]=React.useState(false);
  const [slots,setSlots]=React.useState<Array<{startsAt:string;endsAt:string}>>([]);
  const [slotsMessage,setSlotsMessage]=React.useState('');
  const [slotsBusy,setSlotsBusy]=React.useState(false);
  const slotRequest=React.useRef(0);
  const [clearEmployee,setClearEmployee]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<{path:string;body:string;key:string;success:string;org:string;location:string}|null>(null);
  const requestScope=React.useRef({org:'',location:''});
  requestScope.current={org:org.trim(),location:location.trim()};
  const [revision,setRevision]=React.useState(0);
  const [target,setTarget]=React.useState<string | null>(null);
  const valid=uuid.test(org.trim())&&uuid.test(location.trim())&&validUtcDay(day);
  React.useEffect(()=>{
    setItems([]);setHasMore(false);setNextCursor(null);setMessage('');setTarget(null);setTimeZone('UTC');
    if(!valid){setStatus('idle');return;}
    const controller=new AbortController();
    setStatus('loading');
    // Границы локальных суток (в том числе 23/25 часов) вычисляет сервер.
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/calendar';
    const query=new URLSearchParams({locationId:location.trim(),localDay:day});
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
        if('timeZone' in data&&(typeof data.timeZone!=='string'||data.timeZone.length>64))throw new Error();
        const next=data.hasMore && 'nextStartsAt' in data && typeof data.nextStartsAt==='string' &&
          'nextAppointmentId' in data && typeof data.nextAppointmentId==='string' &&
          uuid.test(data.nextAppointmentId)?{startsAt:data.nextStartsAt,appointmentId:data.nextAppointmentId}:null;
        if(data.hasMore&&!next)throw new Error();
        if(!controller.signal.aborted){setItems(data.items as Entry[]);setHasMore(data.hasMore);setNextCursor(next);setTimeZone('timeZone' in data ? data.timeZone as string : 'UTC');setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[org,location,day,revision,valid,cursor]);
  React.useEffect(()=>{
    const controller=new AbortController();
    setServices([]);setSelectedServices([]);setServiceMessage('');
    if(!uuid.test(org.trim())||!uuid.test(location.trim()))return ()=>controller.abort();
    setServiceMessage('Загрузка каталога работ…');
    void fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
      '/locations/'+encodeURIComponent(location.trim())+'/booking-services',{
        credentials:'include',cache:'no-store',signal:controller.signal,
      }).then(async response=>{
        if(controller.signal.aborted)return;
        if(!response.ok)throw Error('catalog');
        const data:unknown=await response.json();
        if(!data||typeof data!=='object'||!('syntheticOnly' in data)||
          data.syntheticOnly!==true||!('items' in data)||!Array.isArray(data.items)||
          data.items.length>100||!data.items.every((item:Service)=>uuid.test(item.id)&&
            typeof item.code==='string'&&typeof item.title==='string'&&
            Number.isInteger(item.durationMinutes)&&item.durationMinutes>=5&&
            item.durationMinutes<=1440))throw Error('shape');
        if(!controller.signal.aborted){setServices(data.items as Service[]);setServiceMessage('');}
      }).catch(()=>{if(!controller.signal.aborted)setServiceMessage('Каталог услуг недоступен.');});
    return ()=>controller.abort();
  },[org,location]);
  React.useEffect(()=>{
    const controller=new AbortController();
    setStock([]);setMaterials([]);setStockMessage('');
    if(!uuid.test(org.trim())||!uuid.test(location.trim()))return ()=>controller.abort();
    const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
      '/locations/'+encodeURIComponent(location.trim())+'/booking-stock';
    void fetch(url,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async response=>{
        if(controller.signal.aborted)return;
        if(response.status===403){setStockMessage('Для резерва деталей требуются роли INTAKE и WAREHOUSE.');return;}
        if(!response.ok)throw Error('stock');
        const raw:unknown=await response.json();
        if(!raw||typeof raw!=='object'||!('syntheticOnly' in raw)||
          raw.syntheticOnly!==true||!('items' in raw)||!Array.isArray(raw.items)||
          raw.items.length>100||!raw.items.every((item:StockItem)=>
            uuid.test(item.skuId)&&typeof item.article==='string'&&
            Number.isSafeInteger(item.available)&&item.available>=0))throw Error('shape');
        if(!controller.signal.aborted)setStock(raw.items as StockItem[]);
      }).catch(()=>{if(!controller.signal.aborted)setStockMessage('Каталог остатков недоступен.');});
    return ()=>controller.abort();
  },[org,location]);
  React.useEffect(()=>{
    const controller=new AbortController();
    setOpening(null);setOpeningWindows([]);setClosedText('');
    if(!uuid.test(org.trim())||!uuid.test(location.trim())){
      setOpeningState('Укажите филиал для просмотра графика.');
      return ()=>controller.abort();
    }
    setOpeningState('График загружается…');
    const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
      '/locations/'+encodeURIComponent(location.trim())+'/opening-hours';
    void fetch(url,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async response=>{
        if(controller.signal.aborted)return;
        if(response.status===403){setOpeningState('Для просмотра графика требуется INTAKE или MANAGER.');return;}
        if(!response.ok)throw Error('opening');
        const raw:unknown=await response.json();
        if(!raw||typeof raw!=='object'||!('syntheticOnly' in raw)||
          raw.syntheticOnly!==true||!('configured' in raw)||
          typeof raw.configured!=='boolean'||!('version' in raw)||
          (typeof raw.version!=='number'||!Number.isInteger(raw.version))||
          !('windows' in raw)||
          !Array.isArray(raw.windows)||!('closedDates' in raw)||
          !Array.isArray(raw.closedDates)||!('timeZone' in raw)||
          typeof raw.timeZone!=='string')throw Error('shape');
        if(!controller.signal.aborted){
          const policy=raw as OpeningPolicy;
          setOpening(policy);
          setOpeningWindows(policy.windows.map(x=>({
            weekday:x.weekday,open:toClock(x.opensMinute),close:toClock(x.closesMinute),
          })));
          setClosedText(policy.closedDates.join('\n'));
          setOpeningState(policy.configured?'График филиала настроен.':
            'График филиала не настроен: dev-кандидаты не ограничены часами работы.');
        }
      }).catch(()=>{if(!controller.signal.aborted)setOpeningState('Не удалось получить график филиала.');});
    return ()=>controller.abort();
  },[org,location,openingRevision]);
  function resetSlots(){slotRequest.current++;setSlots([]);setSlotsMessage('');setSlotsBusy(false);}
  function resetPage(){setCursor(null);setHistory([]);resetSlots();}
  async function saveOpening(){
    if(!opening||openingBusy||busy||!valid)return;
    const windows=openingWindows.map(x=>({
      weekday:x.weekday,opensMinute:fromClock(x.open),closesMinute:fromClock(x.close),
    }));
    const dates=closedText.split(/[\s,;]+/).filter(Boolean);
    if(windows.length>28||windows.some(x=>!Number.isInteger(x.weekday)||
      !Number.isInteger(x.opensMinute)||!Number.isInteger(x.closesMinute)||
      x.weekday<1||x.weekday>7||x.opensMinute<0||x.opensMinute>=x.closesMinute||
      x.closesMinute>1440)||dates.length>120||
      dates.some(x=>!validUtcDay(x))||new Set(dates).size!==dates.length){
      setOpeningState('Проверьте часы (например, 09:00–18:00), дни и уникальные даты закрытия.');
      return;
    }
    const scopedOrg=org.trim(),scopedLoc=location.trim();
    setOpeningBusy(true);setOpeningState('Сохранение графика…');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if(!csrf.ok)throw Error('csrf');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
        typeof token.csrfToken!=='string')throw Error('csrf');
      const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(scopedOrg)+
        '/locations/'+encodeURIComponent(scopedLoc)+'/opening-hours';
      const response=await fetch(url,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body:JSON.stringify({expectedVersion:opening.version,windows,closedDates:dates}),
      });
      if(requestScope.current.org!==scopedOrg||requestScope.current.location!==scopedLoc)return;
      if(response.status===409){
        setOpeningState('График изменён другим сотрудником или нарушает существующую бронь. Обновите страницу.');return;
      }
      if(response.status===403){setOpeningState('Для изменения графика нужна роль MANAGER.');return;}
      if(!response.ok){setOpeningState('Ошибка графика ('+response.status+').');return;}
      setOpeningState('График сохранён.');setOpeningRevision(n=>n+1);
      resetPage();setRevision(n=>n+1);
    }catch{
      if(requestScope.current.org===scopedOrg&&requestScope.current.location===scopedLoc)
        setOpeningState('Результат неизвестен. Повторно загрузите график перед изменением.');
    }finally{setOpeningBusy(false);}
  }
  React.useEffect(()=>{setPending(null);},[org,location]);
  async function findSlots(){
    const retained=target?items.find(x=>x.appointmentId===target)?.employeeId:null;
    const effectiveEmployee=target&&clearEmployee?null:(form.employee.trim()||retained||null);
    if(!valid||!uuid.test(form.bay.trim())||
       (effectiveEmployee!==null&&!uuid.test(effectiveEmployee)))return;
    const requestId=++slotRequest.current;
    setSlotsBusy(true);setSlots([]);setSlotsMessage('');
    const query=new URLSearchParams({locationId:location.trim(),bayId:form.bay.trim(),localDay:day});
    if(!target)selectedServices.forEach(id=>query.append('serviceId',id));
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
        !('items' in data)||!Array.isArray(data.items)||data.items.length>50||
        !data.items.every((x:unknown)=>x&&typeof x==='object'&&
          'startsAt' in x&&typeof x.startsAt==='string'&&validIsoInstant(x.startsAt)&&
          'endsAt' in x&&typeof x.endsAt==='string'&&validIsoInstant(x.endsAt)&&
          Date.parse(x.endsAt)>Date.parse(x.startsAt)))throw new Error();
      if(requestId!==slotRequest.current)return;
      if('timeZone' in data && data.timeZone!==timeZone)throw new Error();
      setSlots(data.items as Array<{startsAt:string;endsAt:string}>);
      if(data.items.length===0)setSlotsMessage('Подходящих окон нет.');
    }catch{if(requestId===slotRequest.current)setSlotsMessage('Не удалось получить окна записи.');}
    finally{if(requestId===slotRequest.current)setSlotsBusy(false);}
  }

  async function action(path:string,payload:unknown,success:string,
                        retry?:{path:string;body:string;key:string;success:string;org:string;location:string}){
    if(busy||!valid||(pending&&!retry))return;
    const isIdempotent=path.endsWith('/reschedule')||path.endsWith('/appointments')||path.endsWith('/cancel');
    const snapshot=retry??(isIdempotent?{
      path,body:JSON.stringify(payload),key:crypto.randomUUID(),success,
      org:org.trim(),location:location.trim(),
    }:null);
    if(retry&&(retry.org!==org.trim()||retry.location!==location.trim()))return;
    const submittedScope={org:org.trim(),location:location.trim()};
    const scopeIsCurrent=()=>requestScope.current.org===submittedScope.org&&
      requestScope.current.location===submittedScope.location;
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
      if(!scopeIsCurrent())return;
      if(response.status>=500)throw new Error('uncertain server outcome');
      if(!response.ok){
        const errorBody:unknown=await response.json().catch(()=>null);
        if(!scopeIsCurrent())return;
        const code=errorBody&&typeof errorBody==='object'&&'code' in errorBody
          ? errorBody.code:null;
        setPending(null);setMessage(bookingFailure(response.status,code));return;
      }
      setPending(null);setMessage(success);setTarget(null);setClearEmployee(false);setForm(empty);setSelectedServices([]);setMaterials([]);resetPage();setRevision(n=>n+1);
    }catch{
      if(!scopeIsCurrent())return;
      if(snapshot){setPending(snapshot);setMessage('Результат запроса неизвестен. Повторите с прежним ключом или обновите календарь перед новой операцией.');}
      else setMessage('Не удалось выполнить запрос. Проверьте сессию.');
    }finally{setBusy(false);}
  }
  const formatLocal=(instant:string)=>new Intl.DateTimeFormat('ru-RU',{
    timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23',
  }).format(new Date(instant));
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
      {slots.length>0&&<div aria-label="Кандидаты времени"><p>Кандидаты, не гарантия брони:</p><p>Часовой пояс окон: {timeZone}</p><ul>
        {slots.map(slot=><li key={slot.startsAt}><button type="button" onClick={()=>{setForm(v=>({...v,starts:slot.startsAt,ends:slot.endsAt}));resetSlots();}}>
          {formatLocal(slot.startsAt)} — {formatLocal(slot.endsAt)} ({timeZone}); ISO: {slot.startsAt} — {slot.endsAt}</button></li>)}
      </ul></div>}
      <label>Начало (ISO 8601 с часовым поясом) <input aria-label="Начало ISO" value={form.starts}
        onChange={e=>setForm(s=>({...s,starts:e.target.value}))}
        placeholder="2030-01-01T10:00:00+03:00"/></label>
      <label>Конец (ISO 8601 с часовым поясом) <input aria-label="Конец ISO" value={form.ends}
        onChange={e=>setForm(s=>({...s,ends:e.target.value}))}/></label>
    </div>;
  }
  const selectedMinutes=services.filter(x=>selectedServices.includes(x.id)).reduce((a,x)=>a+x.durationMinutes,0);
  const availableMinutes=validIsoInstant(form.starts)&&validIsoInstant(form.ends)?
    Math.max(0,(Date.parse(form.ends)-Date.parse(form.starts))/60000):60;
  const validMaterials=materials.length<=8&&materials.every(item=>{
    const known=stock.find(x=>x.skuId===item.skuId);
    return known&&Number.isSafeInteger(item.quantity)&&item.quantity>=1&&
      item.quantity<=known.available;
  });
  const validForm=(!target?selectedMinutes<=availableMinutes&&validMaterials:true)&&
    (clearEmployee||!form.employee.trim()||uuid.test(form.employee.trim()))&&uuid.test(form.bay.trim())&&(!target?uuid.test(form.link.trim()):true)&&
    validIsoInstant(form.starts)&&validIsoInstant(form.ends)&&
    Date.parse(form.ends)>Date.parse(form.starts)&&
    Date.parse(form.ends)-Date.parse(form.starts)<=86400000&&
    Date.parse(form.starts)>Date.now();
  return <section aria-label="Календарь приёмщика (dev)" style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Календарь приёмщика</h1>
    <p>Синтетический HTTPS-стенд. Поиск записей и свободных окон — по местным суткам филиала с учётом перевода часов. Поле ISO содержит абсолютное время для бронирования. Реальных клиентов не добавляйте.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org} onChange={e=>{resetPage();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={location} onChange={e=>{resetPage();setLocation(e.target.value);}}/></label>
    <label>День филиала <input aria-label="День филиала" type="date" value={day} onChange={e=>{resetPage();setDay(e.target.value);}}/></label>
    <fieldset aria-label="График работы филиала (dev)">
      <legend>Часы работы и закрытые даты</legend>
      <p>Настройка для филиала по его часовому поясу. Просмотр: INTAKE/MANAGER; изменение: только MANAGER. Без настройки dev-режим сохраняет старые круглосуточные кандидаты и не готов к production.</p>
      <p>{openingState}</p>
      {opening&&<>
        <p>Версия {opening.version}; зона {opening.timeZone}. Времена указываются по местным часам.</p>
        {openingWindows.map((window,index)=><div key={index}>
          <label>День интервала {index+1}
            <select aria-label={'День интервала '+(index+1)} value={window.weekday}
              onChange={e=>setOpeningWindows(old=>old.map((w,i)=>i===index?
                {...w,weekday:Number(e.target.value)}:w))}>
              {weekdays.map((d,i)=><option key={d} value={i+1}>{d}</option>)}
            </select>
          </label>
          <label>Открытие {index+1}
            <input aria-label={'Открытие '+(index+1)} value={window.open}
              onChange={e=>setOpeningWindows(old=>old.map((w,i)=>i===index?
                {...w,open:e.target.value}:w))} placeholder="09:00"/>
          </label>
          <label>Закрытие {index+1}
            <input aria-label={'Закрытие '+(index+1)} value={window.close}
              onChange={e=>setOpeningWindows(old=>old.map((w,i)=>i===index?
                {...w,close:e.target.value}:w))} placeholder="18:00"/>
          </label>
          <button type="button" disabled={openingBusy} onClick={()=>setOpeningWindows(old=>
            old.filter((_,i)=>i!==index))}>Удалить интервал {index+1}</button>
        </div>)}
        <button type="button" disabled={openingBusy||openingWindows.length>=28}
          onClick={()=>setOpeningWindows(old=>[...old,{weekday:1,open:'09:00',close:'18:00'}])}>
          Добавить рабочий интервал
        </button>
        <label>Закрытые даты (ISO YYYY-MM-DD, через запятую или строку)
          <textarea aria-label="Закрытые даты" value={closedText}
            onChange={e=>setClosedText(e.target.value)} rows={2}
            placeholder="2030-01-01"/>
        </label>
        <p>Пустой список интервалов закрывает весь филиал после сохранения. Уже подтверждённые будущие брони нельзя сделать недействительными изменением графика.</p>
        <button type="button" disabled={openingBusy||busy}
          onClick={()=>void saveOpening()}>Сохранить график филиала</button>
        <button type="button" disabled={openingBusy}
          onClick={()=>setOpeningRevision(n=>n+1)}>Перезагрузить график</button>
      </>}
    </fieldset>
    {status==='idle'&&<p>Укажите организацию, филиал и день.</p>}
    {status==='loading'&&<p role="status">Загрузка календаря…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или dev API отключён.</p>}
    {status==='error'&&<p role="alert">Ошибка загрузки календаря. <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить</button></p>}
    {status==='ready'&&<><h2>Записи</h2><p>Часовой пояс филиала: {timeZone}</p>
      {hasMore&&<p role="status">Есть дополнительные записи. Перейдите на следующую страницу.</p>}
      {items.length===0?<p>Записей нет.</p>:<ul>{items.map(entry=><li key={entry.appointmentId}>
        <strong>{formatLocal(entry.startsAt)} — {formatLocal(entry.endsAt)} ({timeZone})</strong> · пост {entry.bayId}
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
        else void action(base,{...payload,locationId:location.trim(),customerLinkId:form.link.trim(),
          serviceIds:selectedServices,materialItems:materials},'Запись создана.');
      }}>
        <h2>{target?'Перенести запись':'Новая запись'}</h2>{fields(!target)}
        {!target&&<fieldset aria-label="Работы в новой записи"><legend>Услуги филиала (необязательно)</legend>
          <p>Не более 8 услуг и 24 часов суммарно. Поиск слотов учитывает полную длительность, а создание проверяет совпадение с выбранным интервалом.</p>
          {serviceMessage&&<p>{serviceMessage}</p>}
          {services.map(service=><label key={service.id} style={{display:'block'}}>
            <input type="checkbox" aria-label={'Работа '+service.code}
              checked={selectedServices.includes(service.id)}
              disabled={busy||!!pending||(!selectedServices.includes(service.id)&&
                (selectedServices.length>=8||selectedMinutes+service.durationMinutes>1440))}
              onChange={e=>{resetSlots();setSelectedServices(old=>e.target.checked?[...old,service.id]:
                old.filter(id=>id!==service.id));}}/>
            {service.title} ({service.durationMinutes} мин.)
          </label>)}
        </fieldset>}
        {!target&&<fieldset aria-label="Товары и расходные материалы для резерва">
          <legend>Товары со склада / Материалы (необязательно)</legend>
          <p>Синтетический резерв. Только при двух ролях INTAKE и WAREHOUSE;
            стоимость не рассчитывается, списание не выполняется. Не более восьми SKU.</p>
          {stockMessage&&<p>{stockMessage}</p>}
          {stock.filter(x=>x.available>0).map(item=>{
            const chosen=materials.find(x=>x.skuId===item.skuId);
            return <div key={item.skuId}>
              <label><input type="checkbox" aria-label={'Выбрать SKU '+item.article}
                checked={!!chosen} disabled={busy||!!pending||
                  (!chosen&&materials.length>=8)}
                onChange={e=>setMaterials(old=>e.target.checked?
                  [...old,{skuId:item.skuId,kind:'PART',quantity:1}]:
                  old.filter(x=>x.skuId!==item.skuId))}/>
                {item.article} · доступно {item.available}</label>
              {chosen&&<>
                <label>Тип {item.article}
                  <select aria-label={'Тип SKU '+item.article} value={chosen.kind}
                    disabled={busy||!!pending}
                    onChange={e=>setMaterials(old=>old.map(x=>x.skuId===item.skuId?
                      {...x,kind:e.target.value as BookingMaterial['kind']}:x))}>
                    <option value="PART">Товар со склада</option>
                    <option value="CONSUMABLE">Расходный материал</option>
                  </select>
                </label>
                <label>Количество {item.article}
                  <input aria-label={'Количество SKU '+item.article}
                    type="number" min={1} max={item.available} step={1}
                    value={chosen.quantity} disabled={busy||!!pending}
                    onChange={e=>setMaterials(old=>old.map(x=>x.skuId===item.skuId?
                      {...x,quantity:Number(e.target.value)}:x))}/>
                </label>
              </>}
            </div>;
          })}
        </fieldset>}
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
