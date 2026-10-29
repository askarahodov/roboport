import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Action='issue'|'return';
type Pending={action:Action;org:string;location:string;url:string;body:string;key:string};
export function WarehouseWorkOrders(){
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [lot,setLot]=React.useState('');
  const [quantity,setQuantity]=React.useState('1');
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const [eventId,setEventId]=React.useState('');
  const context=React.useRef({org:'',location:''});
  context.current={org:org.trim(),location:location.trim()};
  React.useEffect(()=>{setPending(null);setMessage('');setEventId('');},[org,location]);
  const valid=[org,location,order,lot].every(x=>uuid.test(x.trim()))&&
    /^[1-9][0-9]*$/.test(quantity)&&Number.isSafeInteger(Number(quantity))&&
    Number(quantity)<=1_000_000_000;
  async function submit(action:Action,retry?:Pending){
    if(busy||(!retry&&(!valid||pending)))return;
    const snap=retry??{
      action,org:org.trim(),location:location.trim(),
      url:'/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
        '/warehouse/work-orders/'+action,
      key:crypto.randomUUID(),
      body:JSON.stringify({locationId:location.trim(),workOrderId:order.trim(),
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
      const field=snap.action==='issue'?'issueId':'returnId';
      if(!result||typeof result!=='object'||!(field in result)||
        typeof (result as Record<string,unknown>)[field]!=='string'||
        !uuid.test((result as Record<string,string>)[field]))throw new Error('invalid response');
      setEventId((result as Record<string,string>)[field]);
      setPending(null);
      setMessage(snap.action==='issue'?'Выдача из партии проведена в складском журнале.':
        'Возврат в исходную партию проведён в складском журнале.');
    }catch{
      if(snap.org===context.current.org&&snap.location===context.current.location){
        setPending(snap);
        setMessage('Неизвестный результат. Повторите с прежним ключом; не создавайте новую операцию.');
      }
    }finally{setBusy(false);}
  }
  return <section className="ui-page" aria-label="Выдача и возврат деталей">
    <h1>Выдача и возврат по наряду (dev)</h1>
    <p>Только синтетический склад. Для обеих операций нужна действующая роль WAREHOUSE филиала.
      Выдача требует ранее созданного резерва по наряду; документ передачи механику пока не реализован.</p>
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
    {busy&&<p role="status">Выполняется складская операция…</p>}
    {pending&&<button type="button" disabled={busy} onClick={()=>void submit(pending.action,pending)}>
      Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {eventId&&<p>Идентификатор события: <code>{eventId}</code></p>}
  </section>;
}
