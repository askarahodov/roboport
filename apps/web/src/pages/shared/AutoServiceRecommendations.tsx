import React from 'react';
import {AutoRecommendationVisit,validVisit,type RepeatVisit} from './AutoRecommendationVisit';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const reasons={PERIODIC:'Плановое ТО',DEFERRED:'Отложенная работа',FOLLOWUP:'Повторная проверка'} as const;
type Reason=keyof typeof reasons;
type Service={id:string;code:string;title:string};
type Item={id:string;serviceId:string;serviceCode:string;title:string;reasonCode:Reason;
  baselineKm:number;dueDate:string;dueKm:number|null;createdAt:string;
  status:'ACTIVE'|'WITHDRAWN';withdrawnAt:string|null;visit?:RepeatVisit|null};
type Snapshot={syntheticOnly:true;releaseAllowed:false;sendAllowed:false;
  workOrderId:string;canManage:boolean;baselineKm:number|null;services:Service[];servicesTruncated?:boolean;items:Item[]};
type Pending={path:string;body:string};
const km=(x:unknown):x is number=>typeof x==='number'&&Number.isInteger(x)&&x>=0&&x<=2000000;
const day=(x:unknown):x is string=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&
  Number.isFinite(Date.parse(x+'T00:00:00Z'))&&new Date(x+'T00:00:00Z').toISOString().slice(0,10)===x;
const stamp=(x:unknown):x is string=>typeof x==='string'&&Number.isFinite(Date.parse(x));
function parse(value:unknown,order:string,customer:boolean):Snapshot{
  if(!value||typeof value!=='object')throw Error('shape');
  const s=value as Snapshot;
  if(s.syntheticOnly!==true||s.releaseAllowed!==false||s.sendAllowed!==false||
    s.workOrderId!==order.toLowerCase()||typeof s.canManage!=='boolean'||
    (customer&&s.canManage)||!(s.baselineKm===null||km(s.baselineKm))||
    !Array.isArray(s.services)||s.services.length>100||(!s.canManage&&s.services.length!==0)||
    !s.services.every(x=>x&&uuid.test(x.id)&&typeof x.code==='string'&&typeof x.title==='string')||
    !Array.isArray(s.items)||s.items.length>100||!s.items.every(x=>x&&uuid.test(x.id)&&
      uuid.test(x.serviceId)&&typeof x.serviceCode==='string'&&typeof x.title==='string'&&
      Object.hasOwn(reasons,x.reasonCode)&&km(x.baselineKm)&&day(x.dueDate)&&
      (x.dueKm===null||km(x.dueKm)&&x.dueKm>x.baselineKm)&&stamp(x.createdAt)&&
      (x.status==='ACTIVE'?x.withdrawnAt===null:x.status==='WITHDRAWN'&&stamp(x.withdrawnAt))&&
      (x.visit==null||validVisit(x.visit))&&
      (!customer||!('actorId' in x)&&!('withdrawnBy' in x))))throw Error('shape');
  if(new Set(s.items.map(x=>x.id)).size!==s.items.length)throw Error('duplicate');
  return s;
}
export function AutoServiceRecommendations({scope,customer=false,onDenied}:{
  scope:{org:string;loc:string;order:string};customer?:boolean;onDenied?:()=>void}){
  const {org,loc,order}=scope;
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/orders/'+encodeURIComponent(order)+'/recommendations';
  const [state,setState]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>(customer?'idle':'loading');
  const [data,setData]=React.useState<Snapshot|null>(null);
  const [service,setService]=React.useState('');
  const [searchText,setSearchText]=React.useState('');
  const [found,setFound]=React.useState<{items:Service[];truncated:boolean}|null>(null);
  const [visitId,setVisitId]=React.useState<string|null>(null);
  const [reason,setReason]=React.useState<Reason>('PERIODIC');
  const [due,setDue]=React.useState('');
  const [targetKm,setTargetKm]=React.useState('');
  const [confirm,setConfirm]=React.useState<string|null>(null);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const operation=React.useRef<AbortController|null>(null);
  const deniedRef=React.useRef(onDenied);deniedRef.current=onDenied;
  const revoke=()=>{
    setData(null);setPending(null);setConfirm(null);setService('');setDue('');setTargetKm('');setFound(null);setVisitId(null);
    setMessage('');setState('denied');deniedRef.current?.();
  };
  async function load(){
    if(operation.current)return;
    const controller=new AbortController();operation.current=controller;
    setData(null);setConfirm(null);setVisitId(null);setFound(null);setService('');setState('loading');
    try{
      const res=await fetch(base+(customer?'/customer':''),{
        credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){revoke();return;}
      if(!res.ok)throw Error('http');
      const parsed=parse(await res.json(),order,customer);
      if(!controller.signal.aborted){setData(parsed);setState('ready');}
    }catch{if(!controller.signal.aborted)setState('error');}
    finally{if(operation.current===controller)operation.current=null;}
  }
  React.useEffect(()=>{
    if(!customer)void load();
    return ()=>{operation.current?.abort();operation.current=null;};
  },[base,customer]);
  async function searchCatalog(){
    if(operation.current||busy||pending||!data?.canManage)return;
    const controller=new AbortController();operation.current=controller;
    setBusy(true);setMessage('');setFound(null);setService('');
    try{
      const res=await fetch(base+'/services?'+new URLSearchParams({q:searchText.trim()}),
        {credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){revoke();return;}
      if(!res.ok)throw Error('catalog');
      const raw=await res.json();
      if(controller.signal.aborted)return;
      if(raw.syntheticOnly!==true||raw.workOrderId!==order.toLowerCase()||
        typeof raw.truncated!=='boolean'||!Array.isArray(raw.items)||raw.items.length>100||
        !raw.items.every((x:Service)=>x&&typeof x.id==='string'&&uuid.test(x.id)&&
          typeof x.code==='string'&&typeof x.title==='string')||
        new Set(raw.items.map((x:Service)=>x.id)).size!==raw.items.length)throw Error('catalog');
      setFound({items:raw.items,truncated:raw.truncated});
    }catch{if(!controller.signal.aborted)setMessage('Не удалось найти услуги. Повторите поиск.');}
    finally{if(operation.current===controller){operation.current=null;if(!controller.signal.aborted)setBusy(false);}}
  }
  async function submit(request:Pending){
    if(customer||!data?.canManage||state!=='ready'||operation.current)return;
    const controller=new AbortController();operation.current=controller;
    setPending(request);setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',
        cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(csrf.status)){revoke();return;}
      if(!csrf.ok)throw Error('csrf');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||typeof token.csrfToken!=='string')
        throw Error('csrf');
      const res=await fetch(request.path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body:request.body,signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){revoke();return;}
      if(res.status===409||res.status===422){
        setPending(null);setConfirm(null);
        setMessage(res.status===409?'Конфликт: перечитайте план. Возможно, активная рекомендация уже есть.':'Проверьте дату, пробег и услугу.');
        setData(null);setState('error');return;
      }
      if(!res.ok)throw Error('unknown');
      const raw=await res.json();
      const parsed=parse(raw,order,false);
      if(controller.signal.aborted)return;
      if(!parsed.canManage||!((res.status===201&&raw.replayed===false)||(res.status===200&&raw.replayed===true)))
        throw Error('receipt');
      const payload=JSON.parse(request.body);
      const target=request.path===base?parsed.items.some(x=>x.serviceId===payload.serviceId&&
        x.reasonCode===payload.reasonCode&&x.dueDate===payload.dueDate&&x.dueKm===payload.dueKm):
        parsed.items.some(x=>request.path===base+'/'+x.id+'/withdraw'&&x.status==='WITHDRAWN');
      if(!target)throw Error('receipt');
      setData(parsed);setPending(null);setConfirm(null);setService('');setDue('');setTargetKm('');setFound(null);
      setMessage(request.path===base?'Рекомендация записана.':'Рекомендация отозвана.');
    }catch{if(!controller.signal.aborted)setMessage('Результат неизвестен. Повторите исходный запрос с тем же ключом.');}
    finally{if(operation.current===controller){operation.current=null;if(!controller.signal.aborted)setBusy(false);}}
  }
  const today=new Date().toISOString().slice(0,10);
  const available=found?.items??data?.services??[];
  const selected=available.find(x=>x.id===service);
  const valid=!!selected&&day(due)&&due>today&&
    (targetKm===''||km(Number(targetKm))&&data?.baselineKm!=null&&Number(targetKm)>data.baselineKm);
  return <section aria-label="План следующего обслуживания" style={{overflowWrap:'anywhere'}}>
    <h3>План следующего обслуживания</h3>
    <p>Рекомендации не изменяют смету, не создают оплату или бронь и не отправляют сообщения.</p>
    {(state==='idle'||state==='error'||state==='denied'||state==='ready')&&
      <button type="button" disabled={busy||pending!==null||visitId!==null} onClick={()=>void load()}>
        {state==='idle'?'Показать рекомендации':'Обновить рекомендации'}</button>}
    {state==='loading'&&<p role="status">Загрузка плана…</p>}
    {state==='denied'&&<p role="alert">Доступ к плану обслуживания запрещён.</p>}
    {state==='error'&&<p role="alert">Не удалось прочитать актуальный план. Обновите рекомендации.</p>}
    {message&&<p role="status">{message}</p>}
    {state==='ready'&&data&&<>
      {data.baselineKm!==null&&<p>Пробег при осмотре: {data.baselineKm.toLocaleString('ru-RU')} км.</p>}
      {data.items.length===0?<p>Рекомендаций пока нет.</p>:<ol>{data.items.map(x=><li key={x.id}>
        <strong>{x.title}</strong> · {reasons[x.reasonCode]} · до {x.dueDate}
        {x.dueKm!==null&&<> или до {x.dueKm.toLocaleString('ru-RU')} км</>}
        {' · '}{x.status==='WITHDRAWN'?'Отозвана':x.dueDate<today?'Срок прошёл':'Запланирована'}
        {x.visit&&<p>Повторный визит: {x.visit.status==='CANCELED'?'Отменён':'Запись оформлена'} ·
          {' '}{new Date(x.visit.startsAt).toLocaleString('ru-RU')}.
          {customer&&<>{' '}<a href="/account/auto-booking">Мои записи на сервис</a></>}</p>}
        {customer&&x.status==='ACTIVE'&&x.visit?.status!=='SCHEDULED'&&
          <button type="button" disabled={visitId!==null} onClick={()=>setVisitId(x.id)}>Записаться по рекомендации</button>}
        {customer&&visitId===x.id&&<AutoRecommendationVisit key={x.id}
          path={base+'/'+x.id+'/visit'} initialDay={x.dueDate} onDenied={revoke}
          onConflict={()=>{setVisitId(null);setMessage('Состояние записи изменилось. План обновлён; при необходимости выберите другое окно.');void load();}}
          onClose={()=>setVisitId(null)}
          onBooked={visit=>{setData(old=>old?{...old,items:old.items.map(row=>row.id===x.id?{...row,visit}:row)}:null);
            setVisitId(null);setMessage('Повторный визит записан.');}}/>}
        {data.canManage&&x.status==='ACTIVE'&&(confirm===x.id?
          <><button type="button" disabled={busy||pending!==null} onClick={()=>void submit({
            path:base+'/'+x.id+'/withdraw',body:JSON.stringify({requestKey:crypto.randomUUID()})})}>
            Подтвердить отзыв рекомендации</button>
            <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
          <button type="button" disabled={busy||pending!==null} onClick={()=>setConfirm(x.id)}>Отозвать рекомендацию</button>)}
      </li>)}</ol>}
      {data.canManage&&(data.baselineKm===null?<p>Сначала зафиксируйте приёмочный осмотр.</p>:
        <form onSubmit={e=>{e.preventDefault();if(valid&&!pending)void submit({path:base,
          body:JSON.stringify({requestKey:crypto.randomUUID(),serviceId:service,reasonCode:reason,
            dueDate:due,dueKm:targetKm===''?null:Number(targetKm)})});}}>
          <h4>Добавить рекомендацию</h4>
          <label>Поиск услуги по коду или названию <input value={searchText} maxLength={80}
            disabled={busy||pending!==null} onChange={e=>setSearchText(e.target.value)}/></label>
          <button type="button" disabled={busy||pending!==null} onClick={()=>void searchCatalog()}>Найти услуги</button>
          {(found?found.truncated:data.servicesTruncated)&&<p>Показаны первые 100 совпадений. Уточните поиск.</p>}
          {found&&found.items.length===0&&<p>Услуги не найдены.</p>}
          <label>Услуга для следующего визита <select value={service} required
            onChange={e=>setService(e.target.value)}><option value="">Выберите услугу</option>
            {available.map(x=><option key={x.id} value={x.id}>{x.code} — {x.title}</option>)}</select></label>
          <label>Основание <select value={reason} onChange={e=>setReason(e.target.value as Reason)}>
            {Object.entries(reasons).map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></label>
          <label>Рекомендуемая дата <input type="date" value={due} min={today} required onChange={e=>setDue(e.target.value)}/></label>
          <label>Следующий пробег (км, необязательно) <input type="number" min={data.baselineKm+1} max={2000000}
            step={1} value={targetKm} onChange={e=>setTargetKm(e.target.value)}/></label>
          <button type="submit" disabled={busy||pending!==null||!valid}>Сохранить рекомендацию</button>
        </form>)}
      {customer&&data.items.some(x=>x.status==='ACTIVE')&&<p>Для следующего визита оформите отдельную запись на обслуживание.
        Выберите время и подтвердите запись. Рекомендация сама по себе не подтверждает свободное время или стоимость работ.</p>}
    </>}
    {pending&&<div role="group" aria-label="Повтор рекомендации">
      <button type="button" disabled={busy} onClick={()=>void submit(pending)}>Повторить исходную операцию</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setMessage('Сверьте план перед новым изменением.');void load();}}>Перечитать план</button>
    </div>}
  </section>;
}
