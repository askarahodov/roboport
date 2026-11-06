import React from 'react';

type Vehicle={make:string;model:string;modelYear:number|null;vinLast4:string|null};
type Appointment={startsAt:string;endsAt:string;status:'SCHEDULED'|'CANCELED'};
type Card={id:string;organizationId:string;locationId:string;createdAt:string;
  vehicle:Vehicle;appointment:Appointment};
type QuoteLine={serviceId:string;code:string;title:string;durationMinutes:number;amountMinor:number};
type Quote={id:string;version:number;currency:'RUB';totalMinor:number;digest:string;
  decision:'APPROVED'|'DECLINED'|null;lines:QuoteLine[];createdAt:string;decidedAt:string|null};
type Work={serviceId:string;title:string;actualMinutes:number};
type Doc={id:string;sha256:string;createdAt:string;type:string};
type Detail=Card&{estimates:Quote[];latestVersion:number;inspection:null|{
  odometerKm:number;fuelPercent:number;tyresOk:boolean;lightsOk:boolean;
  fluidLeak:boolean;exteriorCode:string;createdAt:string
};qcStatus:string;workStatus:string;labor:Work[];document:Doc|null;
  canDecide:boolean;syntheticOnly:true;legalGatePassed:false;releaseAllowed:false};
type Page={items:Card[];nextCursor:string|null;syntheticOnly:true;releaseAllowed:false};
type Pending={path:string;body:string};
type Status='loading'|'ready'|'denied'|'error';
const uid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SIZE=20;
const MAX_PAGES=25;

function validCard(data:unknown):data is Card{
  if(!data||typeof data!=='object')return false;
  const c=data as Record<string,unknown>;
  if(![c.id,c.organizationId,c.locationId].every(x=>typeof x==='string'&&uid.test(x))||
     typeof c.createdAt!=='string'||!c.vehicle||typeof c.vehicle!=='object'||
     !c.appointment||typeof c.appointment!=='object')return false;
  const v=c.vehicle as Record<string,unknown>, a=c.appointment as Record<string,unknown>;
  return typeof v.make==='string'&&typeof v.model==='string'&&
    (v.vinLast4===null||(typeof v.vinLast4==='string'&&v.vinLast4.length===4))&&
    typeof a.startsAt==='string'&&typeof a.endsAt==='string'&&
    (a.status==='SCHEDULED'||a.status==='CANCELED');
}

function parsePage(raw:unknown):Page{
  if(!raw||typeof raw!=='object')throw Error('invalid list');
  const v=raw as Record<string,unknown>;
  if(v.syntheticOnly!==true||v.releaseAllowed!==false||
     !Array.isArray(v.items)||v.items.length>SIZE||!v.items.every(validCard)||
     !(v.nextCursor===null||(typeof v.nextCursor==='string'&&v.nextCursor.length<=256))){
    throw Error('invalid list');
  }
  return v as Page;
}

function parseDetail(raw:unknown):Detail{
  if(!validCard(raw))throw Error('invalid detail');
  const d=raw as unknown as Record<string,unknown>;
  if(d.syntheticOnly!==true||d.releaseAllowed!==false||d.legalGatePassed!==false||
     !Array.isArray(d.estimates)||d.estimates.length>30||
     !Array.isArray(d.labor)||d.labor.length>50||
     typeof d.latestVersion!=='number'||typeof d.workStatus!=='string'||
     typeof d.qcStatus!=='string'||typeof d.canDecide!=='boolean')throw Error('invalid detail');
  return raw as Detail;
}

function date(t:string){
  const stamp=new Date(t);
  return Number.isNaN(stamp.valueOf())?'Дата не определена':stamp.toLocaleString('ru-RU');
}

export function CustomerAutoOrders(){
  const [items,setItems]=React.useState<Card[]>([]);
  const [status,setStatus]=React.useState<Status>('loading');
  const [pages,setPages]=React.useState(1),[refresh,setRefresh]=React.useState(0);
  const [more,setMore]=React.useState(false);
  const [selected,setSelected]=React.useState<string|null>(null);
  const [detail,setDetail]=React.useState<Detail|null>(null);
  const [detailStatus,setDetailStatus]=React.useState<Status>('loading');
  const [busy,setBusy]=React.useState(false),[pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  React.useEffect(()=>{
    const controller=new AbortController();
    setStatus('loading');setItems([]);setMore(false);setMessage('');setPending(null);
    void (async()=>{
      try{
        let cursor:string|null=null;
        const result:Card[]=[];
        let hasNext=false;
        for(let i=0;i<pages;i++){
          const url='/api/v1/dev/auto/customer/orders?limit='+SIZE+
            (cursor?'&cursor='+encodeURIComponent(cursor):'');
          const res=await fetch(url,{credentials:'include',cache:'no-store',
            signal:controller.signal});
          if([401,403,404].includes(res.status)){
            if(!controller.signal.aborted){setItems([]);setSelected(null);
              setDetail(null);setStatus('denied');}return;
          }
          if(!res.ok)throw Error('http');
          const data=parsePage(await res.json());
          if(controller.signal.aborted)return;
          for(const item of data.items){
            if(!result.some(x=>x.id===item.id))result.push(item);
          }
          cursor=data.nextCursor;hasNext=cursor!==null;
          if(cursor===null)break;
        }
        if(controller.signal.aborted)return;
        setItems(result);setMore(hasNext&&pages<MAX_PAGES);setStatus('ready');
        setSelected(previous=>previous&&!result.some(x=>x.id===previous)?null:previous);
      }catch{
        if(!controller.signal.aborted){setStatus('error');setItems([]);setDetail(null);}
      }
    })();
    return ()=>controller.abort();
  },[pages,refresh]);

  React.useEffect(()=>{
    setDetail(null);setPending(null);setMessage('');
    if(!selected)return;
    const controller=new AbortController();
    setDetailStatus('loading');
    void fetch('/api/v1/dev/auto/customer/orders/'+encodeURIComponent(selected),{
      credentials:'include',cache:'no-store',signal:controller.signal,
    }).then(async res=>{
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){
        setDetail(null);setDetailStatus('denied');
        setRefresh(v=>v+1);return;
      }
      if(!res.ok)throw Error('http');
      const data=parseDetail(await res.json());
      if(controller.signal.aborted)return;
      if(data.id!==selected)throw Error('mismatched order');
      setDetail(data);setDetailStatus('ready');
    }).catch(()=>{if(!controller.signal.aborted){
      setDetail(null);setDetailStatus('error');
    }});
    return ()=>controller.abort();
  },[selected,refresh]);

  async function act(path:string,body:string){
    if(!detail||busy||status!=='ready')return;
    setBusy(true);setMessage('');
    try{
      const c=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if(!c.ok)throw Error('csrf');
      const data:unknown=await c.json();
      if(!data||typeof data!=='object'||!('csrfToken' in data)||
        typeof data.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrfToken},
        body});
      if([401,403,404].includes(response.status)){
        setDetail(null);setSelected(null);setItems([]);setStatus('denied');
        setPending(null);return;
      }
      if(response.status===409||response.status===422){
        setPending(null);
        setMessage(response.status===409?
          'Смета изменилась или по ней уже принято решение. Проверьте текущую версию.':
          'Некорректный запрос на согласование.');
        setRefresh(n=>n+1);return;
      }
      if(!response.ok)throw Error('unknown');
      setPending(null);setRefresh(n=>n+1);
    }catch{
      setPending({path,body});
      setMessage('Неизвестный результат. Повторите тот же запрос или перечитайте карточку.');
    }finally{setBusy(false);}
  }

  function decide(value:'APPROVED'|'DECLINED'){
    if(!detail||!detail.canDecide||busy||pending)return;
    const last=detail.estimates[detail.estimates.length-1];
    if(!last)return;
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(detail.organizationId)
      +'/locations/'+encodeURIComponent(detail.locationId)+'/orders/'
      +encodeURIComponent(detail.id)+'/estimates/decision';
    void act(path,JSON.stringify({
      revisionId:last.id,requestKey:crypto.randomUUID(),decision:value,
    }));
  }

  function update(){
    setPages(1);setRefresh(n=>n+1);
  }
  return <section aria-label="Мои автомобильные заказ-наряды">
    <h3>История автомобильного обслуживания</h3>
    <p>Только синтетический стенд. История привязана к заказчику конкретного
      наряда, а не ко всем владельцам автомобиля. Реальной оплаты и выдачи нет.</p>
    <p><a href="/docs/#/guides/client.md">Инструкция по заказ-нарядам и сметам</a></p>
    {status==='loading'&&<p role="status">Загрузка собственных заказ-нарядов…</p>}
    {status==='denied'&&<p role="alert">Доступ к заказ-нарядам запрещён или сессия завершена.</p>}
    {status==='error'&&<p role="alert">Не удалось загрузить заказы. Попробуйте ещё раз.</p>}
    {status==='ready'&&<>
      {items.length===0?<p>Заказ-нарядов с подтверждённой связью пока нет.</p>:
        <ul className="account-records">{items.map(item=><li key={item.id} className="account-record">
          <strong>{item.vehicle.make} {item.vehicle.model}</strong>
          <span>{date(item.appointment.startsAt)} ·
            {' '}{item.appointment.status==='CANCELED'?'Запись отменена':'Запись действующая'}</span>
          <span>{item.vehicle.vinLast4?'VIN ••••'+item.vehicle.vinLast4:'VIN не указан'}</span>
          <button type="button" aria-label={'Подробнее о заказе '+item.id}
            aria-expanded={selected===item.id} onClick={()=>{
              setSelected(selected===item.id?null:item.id);
            }}>{selected===item.id?'Скрыть заказ':'Подробнее о заказе'}</button>
        </li>)}</ul>}
      {more&&<button type="button" onClick={()=>setPages(p=>p+1)}>
        Показать ещё заказ-наряды</button>}
    </>}
    {selected&&status==='ready'&&<section aria-label="Карточка автомобильного заказа"
      className="account-help" aria-live="polite" style={{overflowWrap:"anywhere"}}>
      {detailStatus==='loading'&&<p role="status">Загрузка карточки…</p>}
      {detailStatus==='denied'&&<p role="alert">Карточка недоступна: доступ мог быть отозван.</p>}
      {detailStatus==='error'&&<p role="alert">Не удалось прочитать карточку. Обновите список.</p>}
      {detailStatus==='ready'&&detail&&<>
        <h3>История заказа от {date(detail.createdAt)}</h3>
        <p>Автомобиль: {detail.vehicle.make} {detail.vehicle.model}.
          Бронь: {detail.appointment.status==='CANCELED'?'отменена':'активна'}.
          Начало: {date(detail.appointment.startsAt)}</p>
        <p>Этап ремонта: {detail.workStatus}, контроль качества: {detail.qcStatus}.</p>
        <h4>Приёмочный осмотр</h4>
        {detail.inspection?<p>Пробег: {detail.inspection.odometerKm} км;
          топливо: {detail.inspection.fuelPercent}%.
          Шины: {detail.inspection.tyresOk?'норма':'замечание'},
          освещение: {detail.inspection.lightsOk?'норма':'замечание'},
          утечки: {detail.inspection.fluidLeak?'есть':'нет'}.
        </p>:<p>Осмотр пока не зафиксирован.</p>}
        <p><a href={'/account/auto-intake-act?'+new URLSearchParams({
          org:detail.organizationId,loc:detail.locationId,order:detail.id}).toString()}>
          Посмотреть версии акта приёмки, повреждения и фотографии (dev)</a></p>
        <h4>Сметы и решения</h4>
        {detail.estimates.length===0?<p>Смета ещё не опубликована.</p>:
          <ol>{detail.estimates.map(e=><li key={e.id}>
            <strong>Версия {e.version}: {e.totalMinor} коп. (RUB)</strong>
            <p>Решение: {e.decision??(e.version===detail.latestVersion?
              'ОЖИДАЕТ ВАШЕГО РЕШЕНИЯ':'ЗАМЕНЕНА НОВОЙ ВЕРСИЕЙ')}</p>
            <ul>{e.lines.map(line=><li key={line.serviceId}>
              {line.title}: {line.amountMinor} коп.</li>)}</ul>
          </li>)}</ol>}
        {detail.canDecide&&!pending&&<div>
          <p>Проверьте последнюю версию, перечень работ и сумму перед решением.</p>
          <button type="button" disabled={busy}
            onClick={()=>decide('APPROVED')}>Согласовать последнюю версию</button>
          <button type="button" disabled={busy}
            onClick={()=>decide('DECLINED')}>Отклонить последнюю версию</button>
        </div>}
        <h4>Выполненные работы</h4>
        {detail.labor.length===0?<p>Фактических работ пока нет.</p>:
          <ul>{detail.labor.map(w=><li key={w.serviceId}>
            {w.title}: {w.actualMinutes} мин.</li>)}</ul>}
        <h4>Документы</h4>
        {detail.document?<p>Технический пакет: {detail.document.id}.
          SHA-256: <code>{detail.document.sha256}</code>.
          Не является актом выдачи или фискальным чеком.</p>:
          <p>Технический пакет пока не подготовлен.</p>}
        <p>Выдача автомобиля и реальные платежи недоступны. Юридическая
          и security-приёмка не завершены.</p>
      </>}
      {message&&<p role="alert">{message}</p>}
      {pending&&detail&&<div role="group" aria-label="Повтор неизвестного решения">
        <button type="button" disabled={busy} onClick={()=>void act(pending.path,pending.body)}>
          Повторить то же решение</button>
        <button type="button" disabled={busy} onClick={()=>{
          setPending(null);setRefresh(n=>n+1);
        }}>Перечитать карточку</button>
      </div>}
    </section>}
    {status!=='loading'&&<button type="button" onClick={update}>
      Обновить заказ-наряды</button>}
  </section>;
}
