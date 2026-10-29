import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Action='issue'|'return'|'acknowledge';
type Pending={action:Action;org:string;location:string;url:string;body:string;key:string};
export function WarehouseWorkOrders(){
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [lot,setLot]=React.useState('');
  const [quantity,setQuantity]=React.useState('1');
  const [issueId,setIssueId]=React.useState('');
  const [serialNumber,setSerialNumber]=React.useState('');
  const [serialPending,setSerialPending]=React.useState<{org:string;location:string;body:string;key:string}|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const [eventId,setEventId]=React.useState('');
  const context=React.useRef({org:'',location:''});
  context.current={org:org.trim(),location:location.trim()};
  React.useEffect(()=>{setMessage('');setEventId('');},[org,location]);
  const valid=[org,location,order,lot].every(x=>uuid.test(x.trim()))&&
    /^[1-9][0-9]*$/.test(quantity)&&Number.isSafeInteger(Number(quantity))&&
    Number(quantity)<=1_000_000_000;
  async function submit(action:Action,retry?:Pending){
    if(busy||(!retry&&(!(action==='acknowledge'?uuid.test(org.trim())&&uuid.test(location.trim())&&uuid.test(issueId.trim()):valid)||pending)))return;
    const snap=retry??{
      action,org:org.trim(),location:location.trim(),
      url:'/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
        '/warehouse/work-orders/'+action,
      key:crypto.randomUUID(),
      body:JSON.stringify(action==='acknowledge'?
        {locationId:location.trim(),issueId:issueId.trim()}:
        {locationId:location.trim(),workOrderId:order.trim(),
        lotId:lot.trim(),quantity:Number(quantity)}),
    };
    if(snap.org!==context.current.org||snap.location!==context.current.location)return;
    setBusy(true);setMessage('');setEventId('');
    try{
      const csrfResponse=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store'});
      if(!csrfResponse.ok)throw new Error('csrf unavailable');
      const token:unknown=await csrfResponse.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
        typeof token.csrfToken!=='string')throw new Error('invalid csrf');
      const response=await fetch(snap.url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken,
          'Idempotency-Key':snap.key},body:snap.body});
      if(snap.org!==context.current.org||snap.location!==context.current.location)return;
      if(response.status>=500)throw new Error('uncertain server response');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?'Конфликт резерва, партии или ранее выполненной операции (409).':
          [401,403,404].includes(response.status)?'Нет доступа к операции или синтетический API отключён.':
          'Некорректный запрос ('+response.status+').');
        return;
      }
      const result:unknown=await response.json();
      const field=snap.action==='issue'?'issueId':snap.action==='return'?'returnId':'handoffId';
      if(!result||typeof result!=='object'||!(field in result)||
        typeof (result as Record<string,unknown>)[field]!=='string'||
        !uuid.test((result as Record<string,string>)[field]))throw new Error('invalid response');
      setEventId((result as Record<string,string>)[field]);
      if(snap.action==='issue')setIssueId((result as Record<string,string>)[field]);
      setPending(null);
      setMessage(snap.action==='issue'?'Выдача из партии проведена в складском журнале.':
        snap.action==='return'?'Возврат в исходную партию проведён в складском журнале.':
        'Механик подтвердил получение ранее выданной партии.');
    }catch{
      if(snap.org===context.current.org&&snap.location===context.current.location){
        setPending(snap);
        setMessage('Неизвестный результат. Повторите с прежним ключом; не создавайте новую операцию.');
      }
    }finally{setBusy(false);}
  }
  async function registerSerial(retry?:{org:string;location:string;body:string;key:string}){
    if(busy||(!retry&&serialPending))return;
    const snap=retry??{org:org.trim(),location:location.trim(),
      body:JSON.stringify({locationId:location.trim(),lotId:lot.trim(),serialNumber}),
      key:crypto.randomUUID()};
    if(snap.org!==context.current.org||snap.location!==context.current.location)return;
    setSerialPending(snap);setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw new Error('csrf');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
        typeof token.csrfToken!=='string')throw new Error('csrf');
      const result=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(snap.org)+
        '/warehouse/serials',{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken,
          'Idempotency-Key':snap.key},body:snap.body});
      if(result.status>=500)throw new Error('unknown');
      if(!result.ok){
        setSerialPending(null);setMessage(result.status===409?
          'Серийный номер уже зарегистрирован или превышен остаток партии.':
          'Регистрация отклонена ('+result.status+').');return;
      }
      const body:unknown=await result.json();
      if(!body||typeof body!=='object'||!('serialId' in body)||
        typeof body.serialId!=='string'||!uuid.test(body.serialId))throw new Error('response');
      setSerialPending(null);setEventId(body.serialId);
      setMessage('Серийный номер зарегистрирован за партией.');
    }catch{
      setSerialPending(snap);
      setMessage('Результат регистрации неизвестен. Верните исходные организацию и филиал и повторите с прежним ключом.');
    }finally{setBusy(false);}
  }
  return <section className="ui-page" aria-label="Выдача и возврат деталей">
    <h1>Выдача и возврат по наряду (dev)</h1>
    <p>Только синтетический склад. Для обеих операций нужна действующая роль WAREHOUSE филиала.
      Выдача требует ранее созданного резерва по наряду; электронная подпись и юридически значимый акт передачи пока не реализованы.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={location} onChange={e=>setLocation(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" disabled={!!pending||busy} value={order} onChange={e=>setOrder(e.target.value)}/></label>
    <label>UUID партии <input aria-label="UUID партии" disabled={!!pending||busy} value={lot} onChange={e=>setLot(e.target.value)}/></label>
    <label>Количество <input aria-label="Количество" disabled={!!pending||busy} type="number" min="1" max="1000000000"
      step="1" value={quantity} onChange={e=>setQuantity(e.target.value)}/></label>
    <div>
      <button type="button" disabled={!valid||busy||!!pending} onClick={()=>void submit('issue')}>Выдать из партии</button>
      <button type="button" disabled={!valid||busy||!!pending} onClick={()=>void submit('return')}>Вернуть в партию</button>
    </div>
    <section aria-label="Подтверждение механиком">
      <h2>Подтверждение получения (MECHANIC)</h2>
      <p>Механик входит под собственной сессией и подтверждает уже проведённую выдачу по её UUID.</p>
      <label>UUID выдачи <input aria-label="UUID выдачи" disabled={!!pending||busy}
        value={issueId} onChange={e=>setIssueId(e.target.value)}/></label>
      <button type="button" disabled={busy||!!pending||!uuid.test(org.trim())||
        !uuid.test(location.trim())||!uuid.test(issueId.trim())}
        onClick={()=>void submit('acknowledge')}>Подтвердить получение</button>
    </section>
    <section aria-label="Регистрация серийного номера">
      <h2>Серийный номер партии (dev)</h2>
      <p>Регистрация уникального номера за партией; движения отдельных экземпляров пока не реализованы.</p>
      <label>Серийный номер <input aria-label="Серийный номер" value={serialNumber}
        disabled={busy||!!serialPending} onChange={e=>setSerialNumber(e.target.value)}/></label>
      <button type="button" disabled={busy||!!serialPending||!!pending||
        !uuid.test(org.trim())||!uuid.test(location.trim())||!uuid.test(lot.trim())||
        serialNumber.trim()!==serialNumber||serialNumber.length<3||serialNumber.length>120}
        onClick={()=>void registerSerial()}>Зарегистрировать серийный номер</button>
      {serialPending&&<p role="alert">Результат регистрации не определён; новый ключ запрещён.</p>}
      {serialPending&&<button type="button" disabled={busy||serialPending.org!==org.trim()||
        serialPending.location!==location.trim()} onClick={()=>void registerSerial(serialPending)}>
        Повторить регистрацию с прежним ключом</button>}
    </section>
    {busy&&<p role="status">Выполняется складская операция…</p>}
    {pending&&<p role="alert">Результат операции неизвестен. Исходный ключ сохранён. Верните исходные UUID организации и филиала для повтора; новую операцию запускать нельзя.</p>}
    {pending&&<button type="button" disabled={busy||pending.org!==org.trim()||pending.location!==location.trim()} onClick={()=>void submit(pending.action,pending)}>
      Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {eventId&&<p>Идентификатор события: <code>{eventId}</code></p>}
  </section>;
}
