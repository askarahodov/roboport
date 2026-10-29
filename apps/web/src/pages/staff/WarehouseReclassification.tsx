import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Operation='draft'|'approve';
type Pending={operation:Operation;organization:string;location:string;url:string;body:string;key:string};

export function WarehouseReclassification(){
  const [organization,setOrganization]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [source,setSource]=React.useState('');
  const [destination,setDestination]=React.useState('');
  const [quantity,setQuantity]=React.useState('1');
  const [reason,setReason]=React.useState('');
  const [document,setDocument]=React.useState('');
  const [approval,setApproval]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const current=React.useRef({organization:'',location:''});
  current.current={organization:organization.trim(),location:location.trim()};
  const validScope=uuid.test(organization.trim())&&uuid.test(location.trim());
  const validDraft=validScope&&uuid.test(source.trim())&&uuid.test(destination.trim())&&
    source.trim()!==destination.trim()&&/^[1-9][0-9]*$/.test(quantity)&&
    Number.isSafeInteger(Number(quantity))&&Number(quantity)<=1_000_000_000&&
    reason.trim()===reason&&reason.length>=3&&reason.length<=240;
  const validApprove=validScope&&uuid.test(document.trim());

  async function submit(operation:Operation,retry?:Pending){
    if(busy||(!retry&&(pending||(operation==='draft'?!validDraft:!validApprove))))return;
    const snap=retry??{
      operation,organization:organization.trim(),location:location.trim(),
      url:'/api/v1/dev/auto/organizations/'+encodeURIComponent(organization.trim())+
        '/warehouse/reclassifications'+(operation==='approve'?'/approvals':''),
      key:crypto.randomUUID(),
      body:JSON.stringify(operation==='draft'?
        {locationId:location.trim(),sourceLotId:source.trim(),
          destinationLotId:destination.trim(),quantity:Number(quantity),reason}:
        {locationId:location.trim(),documentId:document.trim()}),
    };
    if(snap.organization!==current.current.organization||
       snap.location!==current.current.location)return;
    // Preserve the exact request/key even when the user edits branch during network I/O.
    setPending(snap);setBusy(true);setMessage('');
    try{
      const csrfResponse=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store'});
      if(!csrfResponse.ok)throw new Error('csrf');
      const token:unknown=await csrfResponse.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
         typeof token.csrfToken!=='string')throw new Error('csrf');
      const response=await fetch(snap.url,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','Idempotency-Key':snap.key,
          'X-CSRF-Token':token.csrfToken},body:snap.body,
      });
      if(response.status>=500)throw new Error('unknown');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?'Конфликт документа, партии или остатков (409).':
          [401,403,404].includes(response.status)?'Нет полномочий или синтетический API отключён.':
          'Некорректные данные ('+response.status+').');
        return;
      }
      const data:unknown=await response.json();
      const field=operation==='draft'?'documentId':'approvalId';
      if(!data||typeof data!=='object'||!(field in data)||
         typeof (data as Record<string,unknown>)[field]!=='string'||
         !uuid.test((data as Record<string,string>)[field]))throw new Error('response');
      if(snap.operation==='draft')setDocument((data as Record<string,string>)[field]);
      else setApproval((data as Record<string,string>)[field]);
      setPending(null);
      setMessage(snap.operation==='draft'?
        'Акт пересортицы создан. Остатки ещё не изменены.':
        'Пересортица утверждена: обе номенклатуры и партии обновлены атомарно.');
    }catch{
      setPending(snap);
      setMessage('Неизвестный результат. Верните исходные организацию и филиал и повторите тот же запрос.');
    }finally{setBusy(false);}
  }

  return <section className="ui-page" aria-label="Пересортица запчастей">
    <h1>Пересортица запчастей (dev)</h1>
    <p>Только синтетический склад. Кладовщик WAREHOUSE готовит акт, другой
      сотрудник MANAGER утверждает. Не заменяет бухгалтерский документ.</p>
    <label>UUID организации <input aria-label="UUID организации" value={organization}
      onChange={e=>setOrganization(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={location}
      onChange={e=>setLocation(e.target.value)}/></label>
    <section aria-label="Акт пересортицы">
      <h2>1. Подготовить акт</h2>
      <label>UUID исходной партии <input aria-label="UUID исходной партии" value={source}
        disabled={busy||!!pending} onChange={e=>setSource(e.target.value)}/></label>
      <label>UUID целевой партии <input aria-label="UUID целевой партии" value={destination}
        disabled={busy||!!pending} onChange={e=>setDestination(e.target.value)}/></label>
      <label>Количество <input aria-label="Количество" type="number" min="1"
        max="1000000000" step="1" value={quantity} disabled={busy||!!pending}
        onChange={e=>setQuantity(e.target.value)}/></label>
      <label>Причина пересортицы <input aria-label="Причина пересортицы"
        maxLength={240} value={reason} disabled={busy||!!pending}
        onChange={e=>setReason(e.target.value)}/></label>
      <button type="button" disabled={!validDraft||busy||!!pending}
        onClick={()=>void submit('draft')}>Создать акт пересортицы</button>
    </section>
    <section aria-label="Утверждение пересортицы">
      <h2>2. Независимое утверждение</h2>
      <label>UUID акта <input aria-label="UUID акта" value={document}
        disabled={busy||!!pending} onChange={e=>setDocument(e.target.value)}/></label>
      <button type="button" disabled={!validApprove||busy||!!pending}
        onClick={()=>void submit('approve')}>Утвердить пересортицу</button>
    </section>
    {busy&&<p role="status">Запрос выполняется…</p>}
    {pending&&<p role="alert">Результат запроса не определён, новый ключ запрещён.</p>}
    {pending&&<button type="button" disabled={busy||
      pending.organization!==organization.trim()||pending.location!==location.trim()}
      onClick={()=>void submit(pending.operation,pending)}>
      Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {approval&&<p>UUID утверждения: {approval}</p>}
    <p>Зарегистрированные серийные номера запрещают количественную пересортицу
      до внедрения поэкземплярного движения. Неотозванные резервы тоже блокируют перенос.</p>
  </section>;
}
