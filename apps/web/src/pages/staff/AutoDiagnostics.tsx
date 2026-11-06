import React from 'react';
import { Link,useSearchParams } from 'react-router-dom';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Finding='NO_FAULT'|'MAINTENANCE_DUE'|'FAULT_CONFIRMED'|'FURTHER_CHECK';
type Severity='ROUTINE'|'SOON'|'URGENT';
type Service={serviceId:string;code:string;title:string;durationMinutes:number};
type Evidence={id:string;serviceId:string;findingCode:Finding;severityCode:Severity;
  actorId:string;createdAt:string};
type State={syntheticOnly:true;releaseAllowed:false;inspectionRecorded:boolean;
  estimatePublished:boolean;services:Service[];items:Evidence[]};
type Pending={path:string;body:string;scope:string};

function parse(raw:unknown):State{
  if(!raw||typeof raw!=='object')throw Error('invalid diagnostics');
  const s=raw as State;
  if(s.syntheticOnly!==true||s.releaseAllowed!==false||
     typeof s.inspectionRecorded!=='boolean'||typeof s.estimatePublished!=='boolean'||
     !Array.isArray(s.services)||s.services.length>50||
     !Array.isArray(s.items)||s.items.length>50||
     !s.services.every(x=>x&&uuid.test(x.serviceId)&&typeof x.code==='string'&&
       typeof x.title==='string'&&Number.isInteger(x.durationMinutes))||
     !s.items.every(x=>x&&[x.id,x.serviceId,x.actorId].every(v=>typeof v==='string'&&uuid.test(v))&&
       ['NO_FAULT','MAINTENANCE_DUE','FAULT_CONFIRMED','FURTHER_CHECK'].includes(x.findingCode)&&
       ['ROUTINE','SOON','URGENT'].includes(x.severityCode)&&
       typeof x.createdAt==='string'&&Number.isFinite(Date.parse(x.createdAt))))
    throw Error('invalid diagnostics');
  return s;
}

const findingLabels:Record<Finding,string>={
  NO_FAULT:'Неисправность не обнаружена',
  MAINTENANCE_DUE:'Требуется плановое обслуживание',
  FAULT_CONFIRMED:'Подтверждена неисправность',
  FURTHER_CHECK:'Требуется дополнительная проверка',
};
const severityLabels:Record<Severity,string>={
  ROUTINE:'Планово',SOON:'В ближайшее время',URGENT:'Срочно',
};

export function AutoDiagnostics(){
  const [query]=useSearchParams();
  const [org,setOrg]=React.useState(query.get('org')??'');
  const [loc,setLoc]=React.useState(query.get('loc')??'');
  const [order,setOrder]=React.useState(query.get('order')??'');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [data,setData]=React.useState<State|null>(null);
  const [loaded,setLoaded]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const [service,setService]=React.useState('');
  const [finding,setFinding]=React.useState<Finding>('FAULT_CONFIRMED');
  const [severity,setSeverity]=React.useState<Severity>('SOON');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const valid=[org,loc,order].every(x=>uuid.test(x.trim()));
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim());
  const ready=status==='ready'&&loaded===scope&&data!==null;
  React.useEffect(()=>{
    setData(null);setLoaded('');setMessage('');setPending(null);
    setStatus(valid?'loading':'idle');
    if(!valid)return;
    const ctrl=new AbortController();
    void fetch(base+'/diagnostics',{credentials:'include',cache:'no-store',signal:ctrl.signal})
      .then(async res=>{
        if(ctrl.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('read');
        const state=parse(await res.json());
        if(!ctrl.signal.aborted){setData(state);setLoaded(scope);setStatus('ready');}
      }).catch(()=>{if(!ctrl.signal.aborted)setStatus('error');});
    return ()=>ctrl.abort();
  },[scope,valid,revision]);

  async function submit(request:Pending){
    if(!ready||busy||request.scope!==scope)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',
        {method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('csrf');
      const obj:unknown=await csrf.json();
      if(!obj||typeof obj!=='object'||!('csrfToken' in obj)||
        typeof obj.csrfToken!=='string')throw Error('csrf');
      const res=await fetch(request.path,{method:'POST',credentials:'include',
        cache:'no-store',headers:{'Content-Type':'application/json',
          'X-CSRF-Token':obj.csrfToken},body:request.body});
      if([401,403,404].includes(res.status)){
        setData(null);setStatus('denied');setPending(null);return;
      }
      if(res.status===409||res.status===422){
        setPending(null);
        setMessage(res.status===409?'Конфликт: осмотр, услуга или смета изменились.':
          'Недопустимые параметры заключения.');
        setRevision(n=>n+1);return;
      }
      if(!res.ok)throw Error('unknown');
      setPending(null);setRevision(n=>n+1);
    }catch{
      setPending(request);
      setMessage('Результат неизвестен. Повторите исходный запрос или перечитайте журнал.');
    }finally{setBusy(false);}
  }

  const annotated=new Set(data?.items.map(x=>x.serviceId));
  const available=data?.services.filter(x=>!annotated.has(x.serviceId))??[];
  const selected=available.find(x=>x.serviceId===service)?.serviceId??available[0]?.serviceId??'';
  const actualSeverity=finding==='NO_FAULT'?'ROUTINE':severity;
  const canSubmit=ready&&!busy&&!pending&&!!data?.inspectionRecorded&&
    !data?.estimatePublished&&uuid.test(selected);
  return <section aria-label="Диагностика автомобильного заказ-наряда (dev)"
    style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Диагностика по услугам заказ-наряда (dev)</h1>
    <p>Структурированное синтетическое заключение. Нет свободного текста, фото,
      юридической подписи или разрешения на выдачу. Заключения становятся частью
      неизменяемого снимка сметы, который отдельно согласует клиент.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      disabled={busy} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      disabled={busy} onChange={e=>setLoc(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" value={order}
      disabled={busy} onChange={e=>setOrder(e.target.value)}/></label>
    {status==='idle'&&<p>Укажите организацию, филиал и заказ-наряд.</p>}
    {status==='loading'&&<p role="status">Загрузка диагностики…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<><p role="alert">Не удалось загрузить заключения.</p>
      <button type="button" onClick={()=>setRevision(v=>v+1)}>Повторить загрузку</button></>}
    {ready&&data&&<>
      <h2>Журнал заключений</h2>
      {data.items.length===0?<p>Диагностических заключений пока нет.</p>:
        <ol>{data.items.map(item=><li key={item.id}>
          {data.services.find(x=>x.serviceId===item.serviceId)?.code??item.serviceId}:
          {' '}{findingLabels[item.findingCode]} · {severityLabels[item.severityCode]}
          {' '}· {new Date(item.createdAt).toLocaleString('ru-RU')}
        </li>)}</ol>}
      {!data.inspectionRecorded&&
        <p role="status">Сначала оформите приёмочный осмотр в разделе «Осмотр и QC».</p>}
      {data.estimatePublished&&
        <p role="status">Смета уже опубликована. Добавление и изменение заключений запрещено.</p>}
      {!data.estimatePublished&&data.inspectionRecorded&&available.length===0&&
        <p>По всем выбранным услугам заключения уже зафиксированы.</p>}
      {!data.estimatePublished&&data.inspectionRecorded&&available.length>0&&<>
        <h3>Неизменяемое заключение (MECHANIC)</h3>
        <label>Услуга для диагностики
          <select aria-label="Услуга для диагностики" value={selected}
            onChange={e=>setService(e.target.value)}>
            {available.map(s=><option key={s.serviceId} value={s.serviceId}>
              {s.code} — {s.title}</option>)}
          </select></label>
        <label>Результат диагностики
          <select aria-label="Результат диагностики" value={finding}
            onChange={e=>setFinding(e.target.value as Finding)}>
            {Object.entries(findingLabels).map(([code,label])=>
              <option key={code} value={code}>{label}</option>)}
          </select></label>
        <label>Приоритет
          <select aria-label="Приоритет диагностики" value={actualSeverity}
            disabled={finding==='NO_FAULT'} onChange={e=>setSeverity(e.target.value as Severity)}>
            {Object.entries(severityLabels).map(([code,label])=>
              <option key={code} value={code}>{label}</option>)}
          </select></label>
        <button type="button" disabled={!canSubmit} onClick={()=>void submit({
          path:base+'/diagnostics',scope,
          body:JSON.stringify({serviceId:selected,findingCode:finding,
            severityCode:actualSeverity,requestKey:crypto.randomUUID()}),
        })}>Зафиксировать заключение</button>
      </>}
      <p>Следующий этап:
        {' '}<Link to={'/staff/auto-estimates?'+new URLSearchParams({
          org:org.trim(),loc:loc.trim(),order:order.trim()}).toString()}>
          Смета и согласование клиента (dev)</Link>.
      </p>
      <button type="button" disabled={busy}
        onClick={()=>setRevision(v=>v+1)}>Обновить журнал</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&ready&&<div role="group" aria-label="Неизвестный результат диагностики">
      <button type="button" disabled={busy} onClick={()=>void submit(pending)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{
        setPending(null);setRevision(v=>v+1);
      }}>Перечитать журнал</button>
    </div>}
  </section>;
}
