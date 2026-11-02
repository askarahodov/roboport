import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Line={serviceId:string;code:string;title:string;durationMinutes:number;amountMinor:number};
type Revision={id:string;version:number;totalMinor:number;currency:'RUB';digest:string;
  lines:Line[];decision:'APPROVED'|'DECLINED'|null;createdAt:string;decidedAt:string|null};
type Estimates={items:Revision[];latestVersion:number;syntheticOnly:true;releaseAllowed:false};
type Service={id:string;code:string;title:string;durationMinutes:number};
type Pending={path:string;body:string};

function parseEstimate(raw:unknown):Estimates{
  if(!raw||typeof raw!=='object')throw Error('invalid estimate');
  const value=raw as Record<string,unknown>;
  if(!Array.isArray(value.items)||value.items.length>30||
    typeof value.latestVersion!=='number'||value.syntheticOnly!==true||
    value.releaseAllowed!==false||!value.items.every((row:unknown)=>{
      if(!row||typeof row!=='object')return false;
      const e=row as Record<string,unknown>;
      return typeof e.id==='string'&&uuid.test(e.id)&&
        typeof e.version==='number'&&Number.isInteger(e.version)&&
        typeof e.totalMinor==='number'&&Number.isSafeInteger(e.totalMinor)&&
        ['APPROVED','DECLINED',null].includes(e.decision as null)&&
        typeof e.digest==='string'&&e.digest.length===64&&
        Array.isArray(e.lines)&&e.lines.length<=50&&
        e.lines.every((line:unknown)=>line!==null&&typeof line==='object'&&
          'code' in line&&typeof line.code==='string'&&
          'title' in line&&typeof line.title==='string'&&
          'amountMinor' in line&&typeof line.amountMinor==='number');
    }))throw Error('invalid estimate');
  return value as Estimates;
}

function parseServices(raw:unknown):Service[]{
  if(!raw||typeof raw!=='object'||!('items' in raw)||!Array.isArray(raw.items)||
    raw.items.length>50||!raw.items.every((s:unknown)=>s!==null&&typeof s==='object'
      &&'id' in s&&typeof s.id==='string'&&uuid.test(s.id)
      &&'code' in s&&typeof s.code==='string'
      &&'title' in s&&typeof s.title==='string'
      &&'durationMinutes' in s&&typeof s.durationMinutes==='number'))throw Error('services');
  return raw.items as Service[];
}

export function AutoEstimateConsole({role}:{role:'manager'|'client'}){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [scopeLoaded,setScopeLoaded]=React.useState('');
  const [data,setData]=React.useState<Estimates|null>(null);
  const [services,setServices]=React.useState<Service[]>([]);
  const [prices,setPrices]=React.useState<Record<string,string>>({});
  const [revision,setRevision]=React.useState(0);
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const valid=[org,loc,order].every(v=>uuid.test(v.trim()));
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())
    +'/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim());
  const ready=status==='ready'&&scopeLoaded===scope&&data!==null;

  React.useEffect(()=>{
    setStatus(valid?'loading':'idle');setData(null);setServices([]);
    setScopeLoaded('');setMessage('');setPending(null);setPrices({});
    if(!valid)return;
    const controller=new AbortController();
    const routes=[base+'/estimates'];
    if(role==='manager')routes.push(base+'/services');
    void Promise.all(routes.map(path=>fetch(path,{credentials:'include',
      cache:'no-store',signal:controller.signal}))).then(async responses=>{
        if(controller.signal.aborted)return;
        if(responses.some(res=>[401,403,404].includes(res.status))){
          setStatus('denied');return;
        }
        if(responses.some(res=>!res.ok))throw Error('request');
        const payloads:unknown[]=await Promise.all(responses.map(res=>res.json()));
        const result=parseEstimate(payloads[0]);
        const selected=role==='manager'?parseServices(payloads[1]):[];
        if(controller.signal.aborted)return;
        setData(result);setServices(selected);setScopeLoaded(scope);setStatus('ready');
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,role,revision,valid]);

  async function send(path:string,body:string){
    if(!ready||busy)return;
    setBusy(true);setMessage('');
    try {
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',cache:'no-store',credentials:'include'});
      if(!csrf.ok)throw Error('csrf');
      const response:unknown=await csrf.json();
      if(!response||typeof response!=='object'||!('csrfToken' in response)||
          typeof response.csrfToken!=='string')throw Error('csrf');
      const result=await fetch(path,{method:'POST',cache:'no-store',credentials:'include',
        headers:{'Content-Type':'application/json','X-CSRF-Token':response.csrfToken},body});
      if([401,403,404].includes(result.status)){
        setStatus('denied');setData(null);setServices([]);setPending(null);return;
      }
      if(!result.ok && result.status!==409 && result.status!==422)throw Error('unknown');
      setPending(null);
      if(!result.ok)setMessage(result.status===409?
        'Конфликт версии или подтверждения. Перечитайте последнюю смету.':
        'Проверьте поля сметы.');
      setRevision(n=>n+1);
    }catch{
      setPending({path,body});
      setMessage('Результат операции неизвестен. Повторите тот же запрос с прежним ключом или перечитайте версии.');
    }finally{setBusy(false);}
  }

  const latest=data?.items[data.items.length-1];
  const canPublish=role==='manager'&&ready&&!busy&&!pending&&services.length>0&&
    !data?.items.some(item=>item.decision!==null)&&services.every(s=>{
      const raw=prices[s.id]??'';
      const amount=Number(raw);
      return raw.trim()!==''&&Number.isSafeInteger(amount)&&amount>=0&&amount<=1e12;
    })&&services.reduce((sum,s)=>sum+Number(prices[s.id]??'0'),0)<=1e12;
  return <section aria-label="Автомобильные сметы (dev)"
    style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>{role==='manager'?'Сметы заказ-наряда (dev)':'Согласование сметы (dev)'}</h1>
    <p>Только синтетические данные. Решение не является юридической электронной подписью,
      оплатой или разрешением на выдачу.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      disabled={busy} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      disabled={busy} onChange={e=>setLoc(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" value={order}
      disabled={busy} onChange={e=>setOrder(e.target.value)}/></label>
    {status==='idle'&&<p>Укажите организацию, филиал и заказ-наряд.</p>}
    {status==='loading'&&<p role="status">Загрузка версий сметы…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<p role="alert">Не удалось прочитать смету.</p>}
    {ready&&data&&<>
      <h2>История смет</h2>
      {data.items.length===0?<p>Опубликованных версий пока нет.</p>:
        <ol>{data.items.map(item=><li key={item.id}>
          <h3>Версия {item.version}: {item.totalMinor} коп. (RUB)</h3>
          <p>Решение: {item.decision??(item.version===data.latestVersion?
            'ОЖИДАЕТ РЕШЕНИЯ':'ЗАМЕНЕНА НОВОЙ ВЕРСИЕЙ')}</p>
          <p>SHA-256 снимка: <code>{item.digest}</code></p>
          <ul>{item.lines.map(line=><li key={line.serviceId}>
            {line.code} — {line.title}: {line.amountMinor} коп.
          </li>)}</ul>
          {role==='client'&&item.version===data.latestVersion&&!item.decision&&!pending&&
            <div><button type="button" disabled={busy} onClick={()=>{
              void send(base+'/estimates/decision',JSON.stringify({
                revisionId:item.id,requestKey:crypto.randomUUID(),decision:'APPROVED'}));
            }}>Согласовать версию {item.version}</button>
            <button type="button" disabled={busy} onClick={()=>{
              void send(base+'/estimates/decision',JSON.stringify({
                revisionId:item.id,requestKey:crypto.randomUUID(),decision:'DECLINED'}));
            }}>Отклонить версию {item.version}</button></div>}
        </li>)}</ol>}
      {role==='manager'&&services.length>0&&!data.items.some(item=>item.decision!==null)&&
        <form onSubmit={event=>{
          event.preventDefault();if(!canPublish)return;
          void send(base+'/estimates',JSON.stringify({
            requestKey:crypto.randomUUID(),expectedVersion:data.latestVersion,
            prices:services.map(service=>({serviceId:service.id,
              amountMinor:Number(prices[service.id])})),
          }));
        }}>
          <h2>Новая неизменяемая версия</h2>
          {services.map(s=><label key={s.id}>{s.code} — {s.title}, стоимость (коп.)
            <input type="number" min="0" max="1000000000000" step="1"
              aria-label={'Стоимость услуги '+s.code}
              value={prices[s.id]??''} onChange={e=>setPrices(prev=>({
                ...prev,[s.id]:e.target.value,
              }))}/></label>)}
          <button type="submit" disabled={!canPublish}>Опубликовать версию</button>
        </form>}
      <button type="button" disabled={busy}
        onClick={()=>setRevision(n=>n+1)}>Обновить версии</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&ready&&<div role="group" aria-label="Повтор неизвестной операции">
      <button type="button" disabled={busy} onClick={()=>void send(pending.path,pending.body)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setRevision(n=>n+1);}}>
        Перечитать версии</button>
    </div>}
    {status==='error'&&<button type="button" onClick={()=>setRevision(n=>n+1)}>
      Повторить загрузку</button>}
  </section>;
}
