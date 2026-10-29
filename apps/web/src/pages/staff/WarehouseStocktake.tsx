import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Pending={path:string;key:string;body:string;kind:'count'|'approval';org:string;location:string};
type Result={countId?:string;approvalId?:string};

export function WarehouseStocktake(){
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [lot,setLot]=React.useState('');
  const [actual,setActual]=React.useState('0');
  const [reason,setReason]=React.useState('');
  const [countId,setCountId]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [result,setResult]=React.useState<Result|null>(null);
  const context=React.useRef({org:'',location:''});
  context.current={org:org.trim(),location:location.trim()};
  React.useEffect(()=>{setMessage('');setPending(null);setResult(null);},[org,location]);
  const scopeValid=uuid.test(org.trim())&&uuid.test(location.trim());
  const countValid=scopeValid&&uuid.test(lot.trim())&&/^(0|[1-9][0-9]*)$/.test(actual)&&
    Number(actual)<=1000000000&&reason.trim().length>=3&&reason.trim().length<=240;
  const approveValid=scopeValid&&uuid.test(countId.trim());
  async function submit(kind:'count'|'approval',retry?:Pending){
    if(busy)return;
    if(!retry&&pending){setMessage('Сначала повторите запрос с прежним ключом либо проверьте журнал перед новым действием.');return;}
    if(!retry&&!(kind==='count'?countValid:approveValid))return;
    const snapshot=retry??{
      org:org.trim(),location:location.trim(),kind,
      path:'/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
        '/stocktake/'+(kind==='count'?'counts':'approvals'),
      key:crypto.randomUUID(),
      body:JSON.stringify(kind==='count'?
        {locationId:location.trim(),lotId:lot.trim(),actual:Number(actual),reason:reason.trim()}:
        {locationId:location.trim(),countId:countId.trim()}),
    };
    if(snapshot.org!==context.current.org||snapshot.location!==context.current.location)return;
    setBusy(true);setMessage('');
    try{
      const token=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!token.ok)throw new Error('csrf');
      const csrf:unknown=await token.json();
      if(!csrf||typeof csrf!=='object'||!('csrfToken' in csrf)||typeof csrf.csrfToken!=='string')throw new Error('csrf');
      const response=await fetch(snapshot.path,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrfToken,
          'Idempotency-Key':snapshot.key},
        body:snapshot.body,
      });
      if(snapshot.org!==context.current.org||snapshot.location!==context.current.location)return;
      if(response.status>=500)throw new Error('uncertain');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?'Конфликт: остаток изменён, запрос уже применён или превышен допустимый объём (409).':
          [401,403,404].includes(response.status)?'Доступ запрещён или отозван (403).':
          'Некорректные данные или ошибка операции ('+response.status+').');
        return;
      }
      const payload:unknown=await response.json();
      const field=snapshot.kind==='count'?'countId':'approvalId';
      if(!payload||typeof payload!=='object'||!(field in payload)||
          typeof (payload as Record<string,unknown>)[field]!=='string'||
          !uuid.test((payload as Record<string,string>)[field]))throw new Error('invalid response');
      const value=(payload as Record<string,string>)[field];
      setResult({[field]:value});setPending(null);
      if(snapshot.kind==='count')setCountId(value);
      setMessage(snapshot.kind==='count'?'Подсчёт сохранён. Остатки пока не изменены.':
        'Расхождение утверждено. Складские проводки записаны.');
    }catch{
      if(snapshot.org===context.current.org&&snapshot.location===context.current.location){
        setPending(snapshot);
        setMessage('Результат неизвестен: повторите с тем же ключом. Не отправляйте новый запрос.');
      }
    }finally{setBusy(false);}
  }
  return <section className="ui-page" aria-label="Инвентаризация склада">
    <header><h1>Инвентаризация склада (dev)</h1>
      <p>Только синтетический контур. Подсчёт — WAREHOUSE, независимое подтверждение — MANAGER.</p></header>
    <div className="crm-filters">
      <label>UUID организации <input value={org} onChange={e=>setOrg(e.target.value)} aria-label="UUID организации"/></label>
      <label>UUID филиала <input value={location} onChange={e=>setLocation(e.target.value)} aria-label="UUID филиала"/></label>
    </div>
    <section aria-label="Создать подсчёт">
      <h2>1. Зафиксировать фактический остаток</h2>
      <p>Подсчёт сохраняется без изменения физического количества.</p>
      <label>UUID партии <input value={lot} onChange={e=>setLot(e.target.value)} aria-label="UUID партии"/></label>
      <label>Фактическое количество <input type="number" min="0" max="1000000000" step="1"
        value={actual} onChange={e=>setActual(e.target.value)} aria-label="Фактическое количество"/></label>
      <label>Причина пересчёта <input value={reason} maxLength={240}
        onChange={e=>setReason(e.target.value)} aria-label="Причина пересчёта"/></label>
      <button type="button" disabled={!countValid||busy||!!pending}
        onClick={()=>void submit('count')}>Сохранить подсчёт</button>
    </section>
    <section aria-label="Утвердить инвентаризацию">
      <h2>2. Независимо утвердить расхождение</h2>
      <p>Для утверждения нужен другой сотрудник с ролью MANAGER. При изменившемся остатке потребуется новый подсчёт.</p>
      <label>UUID подсчёта <input value={countId} onChange={e=>setCountId(e.target.value)}
        aria-label="UUID подсчёта"/></label>
      <button type="button" disabled={!approveValid||busy||!!pending}
        onClick={()=>void submit('approval')}>Утвердить корректировку</button>
    </section>
    {pending&&<button type="button" disabled={busy} onClick={()=>void submit(pending.kind,pending)}>
      Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {result?.countId&&<p>Подсчёт: <code>{result.countId}</code></p>}
    {result?.approvalId&&<p>Утверждение: <code>{result.approvalId}</code></p>}
  </section>;
}
