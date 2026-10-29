import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Pending={path:string;key:string;body:string;kind:'count'|'approval';org:string;location:string};
type Result={countId?:string;approvalId?:string};
type LotRow={lotId:string;article:string;batchCode:string;binCode:string;allocated:number;onHand:number;reserved:number};

export function WarehouseStocktake(){
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [lot,setLot]=React.useState('');
  const [actual,setActual]=React.useState('0');
  const [reason,setReason]=React.useState('');
  const [countId,setCountId]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [lots,setLots]=React.useState<LotRow[]>([]);
  const [lotsStatus,setLotsStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [nextLot,setNextLot]=React.useState<string|null>(null);
  const [lotCursor,setLotCursor]=React.useState<string|null>(null);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [result,setResult]=React.useState<Result|null>(null);
  const context=React.useRef({org:'',location:''});
  context.current={org:org.trim(),location:location.trim()};
  React.useEffect(()=>{setMessage('');setPending(null);setResult(null);setLots([]);setLotsStatus('idle');setNextLot(null);setLotCursor(null);},[org,location]);
  const scopeValid=uuid.test(org.trim())&&uuid.test(location.trim());
  const countValid=scopeValid&&uuid.test(lot.trim())&&/^(0|[1-9][0-9]*)$/.test(actual)&&
    Number(actual)<=1000000000&&reason.trim().length>=3&&reason.trim().length<=240;
  const approveValid=scopeValid&&uuid.test(countId.trim());
  async function loadLots(after:string|null=null){
    if(!scopeValid||lotsStatus==='loading')return;
    const requested={org:org.trim(),location:location.trim()};
    setLotsStatus('loading');setLots([]);
    const params=new URLSearchParams({locationId:requested.location,limit:'30'});
    if(after)params.set('afterLotId',after);
    try{
      const response=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(requested.org)+
        '/stocktake/lots?'+params.toString(),{credentials:'include',cache:'no-store'});
      if(context.current.org!==requested.org||context.current.location!==requested.location)return;
      if([401,403,404].includes(response.status)){setLotsStatus('denied');return;}
      if(!response.ok)throw new Error('unavailable');
      const body:unknown=await response.json();
      if(!body||typeof body!=='object'||!('items' in body)||!Array.isArray(body.items)||
          body.items.length>30||!body.items.every((entry:unknown)=>{
            if(!entry||typeof entry!=='object')return false;
            const x=entry as Record<string,unknown>;
            return uuid.test(String(x.lotId))&&['article','batchCode','binCode'].every(k=>
              typeof x[k]==='string'&&(x[k] as string).length>0)&&
              ['allocated','onHand','reserved'].every(k=>typeof x[k]==='number'&&
                Number.isSafeInteger(x[k])&&(x[k] as number)>=0);
          })||!('nextLotId' in body)||
          !(body.nextLotId===null||(typeof body.nextLotId==='string'&&uuid.test(body.nextLotId))))
        throw new Error('bad payload');
      setLots(body.items as LotRow[]);setNextLot(body.nextLotId as string|null);
      setLotCursor(after);setLotsStatus('ready');
    }catch{if(context.current.org===requested.org&&context.current.location===requested.location)
      setLotsStatus('error');}
  }
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
    <section aria-label="Партии филиала">
      <h2>Партии и остатки</h2>
      <button type="button" disabled={!scopeValid||lotsStatus==='loading'} onClick={()=>void loadLots()}>Показать партии</button>
      {lotsStatus==='loading'&&<p role="status">Загрузка партий…</p>}
      {lotsStatus==='denied'&&<p role="alert">Нет доступа к партиям этого филиала.</p>}
      {lotsStatus==='error'&&<p role="alert">Не удалось загрузить партии. Повторите запрос.</p>}
      {lotsStatus==='ready'&&(lots.length?<ul>{lots.map(item=><li key={item.lotId}>
        <strong>{item.article}</strong> · партия {item.batchCode} · ячейка {item.binCode} ·
        в партии: {item.allocated} · физически: {item.onHand} · резерв: {item.reserved}
        <button type="button" onClick={()=>setLot(item.lotId)}>
          Выбрать партию {item.batchCode}</button>
      </li>)}</ul>:<p>В этом филиале партий нет.</p>)}
      {lotsStatus==='ready'&&nextLot&&<button type="button" disabled={busy}
        onClick={()=>void loadLots(nextLot)}>Следующая страница партий</button>}
      {lotsStatus==='ready'&&lotCursor&&<button type="button"
        onClick={()=>void loadLots()}>Вернуться к первой странице</button>}
    </section>
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
