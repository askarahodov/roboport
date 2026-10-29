import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Action='issue'|'return'|'transfer';
type Pending={action:Action;organization:string;location:string;url:string;body:string;key:string};

export function WarehouseSerialMovements(){
  const [organization,setOrganization]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [serial,setSerial]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [destination,setDestination]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [last,setLast]=React.useState('');
  const context=React.useRef({organization:'',location:''});
  context.current={organization:organization.trim(),location:location.trim()};

  const scopeValid=uuid.test(organization.trim())&&uuid.test(location.trim())&&uuid.test(serial.trim());
  async function submit(action:Action,retry?:Pending){
    if(busy||(!retry&&(pending||!scopeValid||
      (action==='transfer'?!uuid.test(destination.trim()):!uuid.test(order.trim())))))return;
    const snap=retry??{
      action,organization:organization.trim(),location:location.trim(),
      url:'/api/v1/dev/auto/organizations/'+encodeURIComponent(organization.trim())+
        '/warehouse/serial-movements/'+action,
      key:crypto.randomUUID(),
      body:JSON.stringify(action==='transfer'?{
        locationId:location.trim(),serialId:serial.trim(),
        destinationLotId:destination.trim(),
      }:{
        locationId:location.trim(),serialId:serial.trim(),workOrderId:order.trim(),
      }),
    };
    if(snap.organization!==context.current.organization||
       snap.location!==context.current.location)return;
    // Retain immutable request even when source branch changes while network is pending.
    setPending(snap);setBusy(true);setMessage('');setLast('');
    try{
      const responseCsrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',cache:'no-store',credentials:'include'});
      if(!responseCsrf.ok)throw new Error('csrf');
      const csrf:unknown=await responseCsrf.json();
      if(!csrf||typeof csrf!=='object'||!('csrfToken' in csrf)||
         typeof csrf.csrfToken!=='string')throw new Error('csrf');
      const response=await fetch(snap.url,{
        method:'POST',cache:'no-store',credentials:'include',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrfToken,
          'Idempotency-Key':snap.key},body:snap.body,
      });
      if(response.status>=500)throw new Error('server');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?
          'Конфликт состояния серийной детали, заказа или уже выполненной операции (409).':
          [401,403,404].includes(response.status)?
            'Нет складского доступа к исходному и целевому филиалам либо dev API отключён.':
            'Недопустимый запрос ('+response.status+').');
        return;
      }
      const data:unknown=await response.json();
      if(!data||typeof data!=='object'||!('movementId' in data)||
         typeof data.movementId!=='string'||!uuid.test(data.movementId))
        throw new Error('unknown response');
      setLast(data.movementId);setPending(null);
      setMessage(snap.action==='issue'?'Серийная деталь выдана по заказ-наряду.':
        snap.action==='return'?'Серийная деталь возвращена в исходную партию.':
        'Серийная деталь перемещена в другой филиал.');
    }catch{
      setPending(snap);
      setMessage('Результат операции неизвестен. Верните исходные организацию и филиал и повторите прежний ключ.');
    }finally{setBusy(false);}
  }
  return <section className="ui-page" aria-label="Движения серийных деталей">
    <h1>Движения серийных деталей (dev)</h1>
    <p>Только синтетический склад. Один серийный номер — одна физическая деталь.
      Выдача требует резерв заказ-наряда. Для межфилиального перемещения требуются права
      WAREHOUSE обоих филиалов.</p>
    <label>UUID организации <input aria-label="UUID организации" value={organization}
      onChange={e=>setOrganization(e.target.value)}/></label>
    <label>UUID исходного филиала <input aria-label="UUID исходного филиала" value={location}
      onChange={e=>setLocation(e.target.value)}/></label>
    <label>UUID серийной детали <input aria-label="UUID серийной детали" value={serial}
      disabled={busy||!!pending} onChange={e=>setSerial(e.target.value)}/></label>
    <section aria-label="Выдача и возврат">
      <h2>Выдача и возврат по заказ-наряду</h2>
      <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" value={order}
        disabled={busy||!!pending} onChange={e=>setOrder(e.target.value)}/></label>
      <button type="button" disabled={busy||!!pending||!scopeValid||!uuid.test(order.trim())}
        onClick={()=>void submit('issue')}>Выдать серийную деталь</button>
      <button type="button" disabled={busy||!!pending||!scopeValid||!uuid.test(order.trim())}
        onClick={()=>void submit('return')}>Вернуть серийную деталь</button>
    </section>
    <section aria-label="Межфилиальное перемещение">
      <h2>Перемещение в другой филиал</h2>
      <label>UUID партии назначения <input aria-label="UUID партии назначения"
        value={destination} disabled={busy||!!pending}
        onChange={e=>setDestination(e.target.value)}/></label>
      <button type="button" disabled={busy||!!pending||!scopeValid||!uuid.test(destination.trim())}
        onClick={()=>void submit('transfer')}>Переместить серийную деталь</button>
    </section>
    {busy&&<p role="status">Операция выполняется…</p>}
    {pending&&<p role="alert">Результат операции не определён; новый ключ запрещён.</p>}
    {pending&&<button type="button" disabled={busy||
       pending.organization!==organization.trim()||pending.location!==location.trim()}
       onClick={()=>void submit(pending.action,pending)}>Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {last&&<p>UUID движения: {last}</p>}
  </section>;
}
