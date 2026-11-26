import React from 'react';
import {ServiceSurvey} from '../shared/ServiceSurvey';
import {AutoServiceRecommendations} from '../shared/AutoServiceRecommendations';

import {parseCustomerOrderPage,parseCustomerOrderDetail,parseCustomerQuoteReceipt} from '../../shared/api/autoCustomerOrdersDevClient';
import type {Card,Detail,Reason} from '../../shared/api/autoCustomerOrdersDevClient';

type Pending={path:string;body:string};
type Status='loading'|'ready'|'denied'|'error';
const SIZE=20;
const clarificationLabels:Record<Reason,string>={PRICE:'Стоимость',WORK_SCOPE:'Состав работ',TIMING:'Сроки',OTHER:'Другой вопрос'};
const findingLabels={NO_FAULT:'Неисправность не выявлена',MAINTENANCE_DUE:'Требуется обслуживание',FAULT_CONFIRMED:'Неисправность подтверждена',FURTHER_CHECK:'Нужна дополнительная проверка'};
const severityLabels={ROUTINE:'Планово',SOON:'В ближайшее время',URGENT:'Срочно'};
const qualificationLabels={GENERAL:'Общие работы',DIAGNOSTIC:'Диагностика',MASTER:'Мастер'};
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
  const [clarificationReason,setClarificationReason]=React.useState<Reason>('PRICE');
  const generation=React.useRef(0);
  const listAbort=React.useRef<AbortController|null>(null);
  const detailAbort=React.useRef<AbortController|null>(null);
  function invalidate(){generation.current++;listAbort.current?.abort();detailAbort.current?.abort();}
  function deny(){
    invalidate();setItems([]);setMore(false);setSelected(null);setDetail(null);
    setPending(null);setMessage('');setBusy(false);setStatus('denied');setDetailStatus('denied');
  }
  React.useEffect(()=>()=>{invalidate();},[]);
  React.useEffect(()=>{
    const controller=new AbortController();
    listAbort.current=controller;const epoch=++generation.current;
    detailAbort.current?.abort();setDetail(null);
    setStatus('loading');setItems([]);setMore(false);setPending(null);
    void (async()=>{
      try{
        let cursor:string|null=null;
        const result:Card[]=[];
        let hasNext=false;
        const cursors=new Set<string>();
        for(let i=0;i<pages;i++){
          const url='/api/v1/dev/auto/customer/orders?limit='+SIZE+
            (cursor?'&cursor='+encodeURIComponent(cursor):'');
          const res=await fetch(url,{credentials:'include',cache:'no-store',
            signal:controller.signal});
          if(controller.signal.aborted||epoch!==generation.current)return;
          if([401,403,404].includes(res.status)){deny();return;}
          if(!res.ok)throw Error('http');
          const data=parseCustomerOrderPage(await res.json(),SIZE);
          if(controller.signal.aborted)return;
          for(const item of data.items){
            if(!result.some(x=>x.id===item.id))result.push(item);
          }
          cursor=data.nextCursor;hasNext=cursor!==null;
          if(cursor===null)break;
          if(cursors.has(cursor)||data.items.length===0)throw Error('cursor');
          cursors.add(cursor);
        }
        if(controller.signal.aborted)return;
        setItems(result);setMore(hasNext);setStatus('ready');
        setSelected(previous=>previous&&!result.some(x=>x.id===previous)?null:previous);
      }catch{
        if(!controller.signal.aborted){detailAbort.current?.abort();setSelected(null);setStatus('error');setItems([]);setDetail(null);}
      }
    })();
    return ()=>controller.abort();
  },[pages,refresh]);

  React.useEffect(()=>{
    setDetail(null);setPending(null);
    if(!selected||status!=='ready')return;
    const expected=items.find(x=>x.id===selected);
    if(!expected)return;
    setClarificationReason('PRICE');
    const controller=new AbortController();
    detailAbort.current=controller;
    const epoch=generation.current;
    const current=()=>!controller.signal.aborted&&epoch===generation.current;
    setDetailStatus('loading');
    void fetch('/api/v1/dev/auto/customer/orders/'+encodeURIComponent(selected),{
      credentials:'include',cache:'no-store',signal:controller.signal,
    }).then(async res=>{
      if(!current())return;
      if([401,403,404].includes(res.status)){
        deny();return;
      }
      if(!res.ok)throw Error('http');
      const data=parseCustomerOrderDetail(await res.json(),expected);
      if(!current())return;
      if(data.id!==selected)throw Error('mismatched order');
      setDetail(data);setDetailStatus('ready');
    }).catch(()=>{if(current()){
      setDetail(null);setDetailStatus('error');
    }});
    return ()=>controller.abort();
  },[selected,refresh,status]);

  async function act(path:string,body:string){
    if(!detail||busy||status!=='ready')return;
    const epoch=generation.current;
    const current=()=>epoch===generation.current;
    setBusy(true);setMessage('');
    try{
      const c=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if(!current())return;
      if([401,403,404].includes(c.status)){deny();return;}
      if(!c.ok)throw Error('csrf');
      const data:unknown=await c.json();
      if(!data||typeof data!=='object'||!('csrfToken' in data)||
        typeof data.csrfToken!=='string')throw Error('csrf');
      if(!current())return;
      const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrfToken},
        body});
      if(!current())return;
      if([401,403,404].includes(response.status)){deny();return;}
      if(response.status===409||response.status===422){
        setPending(null);
        setMessage(response.status===409?
          'Смета изменилась или по ней уже принято решение. Проверьте текущую версию.':
          'Некорректный запрос на согласование.');
        setStatus('loading');setRefresh(n=>n+1);return;
      }
      if(!response.ok)throw Error('unknown');
      parseCustomerQuoteReceipt(await response.json(),body);
      if(!current())return;
      setPending(null);setStatus('loading');setRefresh(n=>n+1);
    }catch{
      if(!current())return;
      setPending({path,body});
      setMessage('Неизвестный результат. Повторите тот же запрос или перечитайте карточку.');
    }finally{if(current())setBusy(false);}
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

  const last=detail?.estimates.at(-1);
  const canClarify=!!detail&&!!last&&detail.appointment.status==='SCHEDULED'&&last.decision===null&&last.clarificationId===null;
  function clarify(){
    if(!detail||!last||!canClarify||busy||pending)return;
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(detail.organizationId)
      +'/locations/'+encodeURIComponent(detail.locationId)+'/orders/'+encodeURIComponent(detail.id)+'/estimates/clarification';
    void act(path,JSON.stringify({revisionId:last.id,requestKey:crypto.randomUUID(),reasonCode:clarificationReason}));
  }

  function update(){
    invalidate();setStatus('loading');setMessage('');setPages(1);setRefresh(n=>n+1);
  }
  return <section aria-label="Мои автомобильные заказ-наряды">
    <h3>История автомобильного обслуживания</h3>
    <p>Только синтетический стенд. История привязана к заказчику конкретного
      наряда, а не ко всем владельцам автомобиля. Реальной оплаты и выдачи нет.</p>
    <p><a href="/docs/#/guides/client.md">Инструкция по заказ-нарядам и сметам</a></p>
    {status==='loading'&&<p role="status">Загрузка собственных заказ-нарядов…</p>}
    {status==='denied'&&<p role="alert">Доступ к заказ-нарядам запрещён или сессия завершена. <span>Карточка недоступна: доступ мог быть отозван.</span></p>}
    {status==='error'&&<p role="alert">Не удалось загрузить заказы. Попробуйте ещё раз.</p>}
    {status==='ready'&&<>
      {items.length===0?<p>Заказ-нарядов с подтверждённой связью пока нет.</p>:
        <ul className="account-records">{items.map(item=><li key={item.id} className="account-record">
          <strong>{item.vehicle.make} {item.vehicle.model}</strong>
          <span>{date(item.appointment.startsAt)} ·
            {' '}{item.appointment.status==='CANCELED'?'Запись отменена':'Запись действующая'}</span>
          <span>{item.vehicle.vinLast4?'VIN ••••'+item.vehicle.vinLast4:'VIN не указан'}</span>
          <button type="button" aria-label={'Подробнее о заказе '+item.id}
            aria-expanded={selected===item.id} disabled={busy||!!pending} onClick={()=>{
              detailAbort.current?.abort();setDetail(null);setDetailStatus('loading');setMessage('');
              setSelected(selected===item.id?null:item.id);
            }}>{selected===item.id?'Скрыть заказ':'Подробнее о заказе'}</button>
        </li>)}</ul>}
      {more&&<button type="button" disabled={busy||!!pending} onClick={()=>{invalidate();setStatus('loading');setPages(p=>p+1);}}>
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
              <span>{line.title}: {line.amountMinor} коп.</span>
              {'pricingMethod' in line&&(line.pricingMethod==='NORM_HOUR'||line.pricingMethod==='NORM_HOUR_FACTORED')&&<p>{qualificationLabels[line.qualificationCode]}:
                {' '}{line.durationMinutes} мин., ставка {line.rateMinorPerHour} коп./ч.
                {line.pricingMethod==='NORM_HOUR_FACTORED'&&<>
                  {' '}Количество {line.quantity}, коэффициент {line.coefficientBps/10000},
                  скидка {line.discountBps/100}%.</>}
              </p>}
              {e.diagnostics.filter(d=>d.serviceId===line.serviceId).map(d=>
                <p key={d.serviceId}>{findingLabels[d.findingCode]} · {severityLabels[d.severityCode]}</p>)}
            </li>)}</ul>
            {!!e.materialLines.length&&<><p>Запчасти и расходные материалы</p>
              <ul>{e.materialLines.map((m,i)=><li key={m.selectionId+':'+m.skuId+':'+i}>
                {m.article} · {m.itemKind==='PART'?'Запчасть':'Расходный материал'} ·
                {' '}{m.quantity} ед. × {m.unitPriceMinor} коп. = {m.amountMinor} коп.
              </li>)}</ul></>}
            {e.clarificationId!==null&&<p>Уточнение: {clarificationLabels[e.clarificationReason!]} ·
              {' '}{e.clarificationResolution==='EXPLAINED'?'Получено разъяснение':
                e.clarificationResolution==='NEW_VERSION_REQUIRED'?'Ожидается новая версия':'Ожидается ответ управляющего'}.</p>}
          </li>)}</ol>}
        {canClarify&&!pending&&<div>
          <label>Причина уточнения сметы <select disabled={busy} value={clarificationReason}
            onChange={e=>setClarificationReason(e.target.value as Reason)}>
            {Object.entries(clarificationLabels).map(([id,label])=><option key={id} value={id}>{label}</option>)}
          </select></label>
          <button type="button" disabled={busy} onClick={clarify}>Запросить уточнение последней версии</button>
        </div>}
        {detail.canDecide&&!pending&&<div>
          <p>Проверьте последнюю версию, перечень работ, материалы, диагностику и полную сумму перед решением.</p>
          <button type="button" disabled={busy}
            onClick={()=>decide('APPROVED')}>Согласовать последнюю версию</button>
          <button type="button" disabled={busy}
            onClick={()=>decide('DECLINED')}>Отклонить последнюю версию</button>
        </div>}
        <h4>Выполненные работы</h4>
        {detail.labor.length===0?<p>Фактических работ пока нет.</p>:
          <ul>{detail.labor.map(w=><li key={w.serviceId}>
            {w.title}: {w.actualMinutes} мин.</li>)}</ul>}
        <AutoServiceRecommendations key={detail.id} customer scope={{org:detail.organizationId,loc:detail.locationId,order:detail.id}}
          onDenied={deny}/>
        <ServiceSurvey key={'survey:'+detail.id} customer path={'/api/v1/dev/auto/organizations/'+detail.organizationId+
          '/locations/'+detail.locationId+'/orders/'+detail.id+'/survey/customer'} order={detail.id} onDenied={deny}/>
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
          {pending.path.endsWith('/clarification')?'Повторить то же уточнение':'Повторить то же решение'}</button>
        <button type="button" disabled={busy} onClick={()=>{
          setPending(null);update();
        }}>Перечитать карточку</button>
      </div>}
    </section>}
    {status!=='loading'&&<button type="button" disabled={busy} onClick={update}>
      Обновить заказ-наряды</button>}
  </section>;
}
