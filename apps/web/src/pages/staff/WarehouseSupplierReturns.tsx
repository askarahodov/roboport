import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Action='draft'|'approve';
type Pending={action:Action;org:string;loc:string;key:string;body:string;url:string};

export function WarehouseSupplierReturns(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [receipt,setReceipt]=React.useState('');
  const [qty,setQty]=React.useState('1');
  const [reason,setReason]=React.useState('');
  const [reference,setReference]=React.useState('');
  const [document,setDocument]=React.useState('');
  const [approval,setApproval]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const scope=React.useRef({org:'',loc:''});
  scope.current={org:org.trim(),loc:loc.trim()};
  const validScope=uuid.test(org.trim())&&uuid.test(loc.trim());
  const canDraft=validScope&&uuid.test(receipt.trim())&&
    /^[1-9][0-9]*$/.test(qty)&&Number.isSafeInteger(Number(qty))&&
    Number(qty)<=1000000000&&reason.trim()===reason&&
    reason.length>=3&&reason.length<=240&&reference.trim()===reference&&
    reference.length>=3&&reference.length<=160;
  const canApprove=validScope&&uuid.test(document.trim());

  async function send(action:Action,replay?:Pending){
    if(busy||(!replay&&(pending||(action==='draft'?!canDraft:!canApprove))))return;
    const snap:Pending=replay??{
      action,org:org.trim(),loc:loc.trim(),key:crypto.randomUUID(),
      url:'/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
        '/warehouse/supplier-returns'+(action==='approve'?'/approvals':''),
      body:JSON.stringify(action==='draft'?{
        locationId:loc.trim(),sourceReceiptId:receipt.trim(),
        quantity:Number(qty),reason,supplierReference:reference
      }:{locationId:loc.trim(),documentId:document.trim()})
    };
    if(snap.org!==scope.current.org||snap.loc!==scope.current.loc)return;
    setPending(snap);setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',
        {method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw new Error('csrf');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
        typeof token.csrfToken!=='string')throw new Error('csrf');
      const result=await fetch(snap.url,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken,
          'Idempotency-Key':snap.key},body:snap.body});
      if(result.status>=500)throw new Error('unknown');
      if(!result.ok){
        setPending(null);
        setMessage(result.status===409?
          'Конфликт: остаток, регистрация серии, оценка прихода или повтор запроса.':
          result.status===403?'Нет действующих полномочий на филиал.':
          'Операция отклонена ('+result.status+').');
        return;
      }
      const data:unknown=await result.json();
      const key=action==='draft'?'documentId':'approvalId';
      if(!data||typeof data!=='object'||!(key in data)||
        typeof (data as Record<string,unknown>)[key]!=='string'||
        !uuid.test((data as Record<string,string>)[key]))throw new Error('response');
      if(snap.action==='draft')setDocument((data as Record<string,string>)[key]);
      else setApproval((data as Record<string,string>)[key]);
      setPending(null);
      setMessage(snap.action==='draft'?
        'Акт возврата поставщику создан, остаток ещё не изменён.':
        'Возврат поставщику утверждён: физический остаток и закупочная оценка связаны.');
    }catch{
      setPending(snap);
      setMessage('Результат неизвестен. Верните прежние организацию и филиал и повторите с тем же ключом.');
    }finally{setBusy(false);}
  }

  return <section className="ui-page" aria-label="Возврат поставщику">
    <h1>Возврат поставщику (dev)</h1>
    <p>Только синтетический склад: WAREHOUSE готовит акт по принятому и
      оценённому приходу, другой сотрудник MANAGER утверждает физическое выбытие.</p>
    <label>UUID организации <input aria-label="UUID организации"
      value={org} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала"
      value={loc} onChange={e=>setLoc(e.target.value)}/></label>
    <section aria-label="Подготовка возврата">
      <h2>1. Подготовить возврат</h2>
      <label>UUID принятого прихода <input aria-label="UUID принятого прихода"
        value={receipt} disabled={busy||!!pending}
        onChange={e=>setReceipt(e.target.value)}/></label>
      <label>Количество <input aria-label="Количество" type="number"
        min="1" max="1000000000" step="1" value={qty}
        disabled={busy||!!pending} onChange={e=>setQty(e.target.value)}/></label>
      <label>Причина <input aria-label="Причина"
        value={reason} maxLength={240} disabled={busy||!!pending}
        onChange={e=>setReason(e.target.value)}/></label>
      <label>Номер обращения поставщику <input
        aria-label="Номер обращения поставщику" value={reference}
        maxLength={160} disabled={busy||!!pending}
        onChange={e=>setReference(e.target.value)}/></label>
      <button type="button" disabled={!canDraft||!!pending||busy}
        onClick={()=>void send('draft')}>Создать акт возврата</button>
    </section>
    <section aria-label="Утверждение возврата">
      <h2>2. Независимое утверждение</h2>
      <label>UUID акта <input aria-label="UUID акта"
        value={document} disabled={busy||!!pending}
        onChange={e=>setDocument(e.target.value)}/></label>
      <button type="button" disabled={!canApprove||busy||!!pending}
        onClick={()=>void send('approve')}>Утвердить возврат</button>
    </section>
    {busy&&<p role="status">Выполняется…</p>}
    {pending&&<p role="alert">Неизвестный результат, новый ключ запрещён.</p>}
    {pending&&<button type="button"
      disabled={busy||pending.org!==org.trim()||pending.loc!==loc.trim()}
      onClick={()=>void send(pending.action,pending)}>
      Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {approval&&<p>UUID утверждения: {approval}</p>}
    <p>Закупочная стоимость и входной НДС учитываются раздельно как
      происхождение оценки, не как подтверждённое возмещение или налоговая корректировка.
      Требуется юридически действительный документ поставщика до production.</p>
  </section>;
}
