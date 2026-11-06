import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Action='draft'|'approve';
type Pending={action:Action;org:string;loc:string;key:string;body:string;url:string};
type ReviewItem={documentId:string;sourceReceiptId:string;lotId:string;
  article:string;batchCode:string;binCode:string;quantity:number;
  reason:string;supplierReference:string};
type ReviewState={status:'idle'|'loading'|'ready'|'denied'|'error';
  items:ReviewItem[];nextDocumentId:string|null;cursor:string|null};
const emptyReview=():ReviewState=>({
  status:'idle',items:[],nextDocumentId:null,cursor:null,
});

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
  const [review,setReview]=React.useState<ReviewState>(emptyReview);
  const reviewSequence=React.useRef(0);
  const scope=React.useRef({org:'',loc:''});
  scope.current={org:org.trim(),loc:loc.trim()};
  const validScope=uuid.test(org.trim())&&uuid.test(loc.trim());
  const canDraft=validScope&&uuid.test(receipt.trim())&&
    /^[1-9][0-9]*$/.test(qty)&&Number.isSafeInteger(Number(qty))&&
    Number(qty)<=1000000000&&reason.trim()===reason&&
    reason.length>=3&&reason.length<=240&&reference.trim()===reference&&
    reference.length>=3&&reference.length<=160;
  const canApprove=validScope&&uuid.test(document.trim());
  React.useEffect(()=>{
    reviewSequence.current+=1;
    setReview(emptyReview());
    setDocument('');setApproval('');
  },[org,loc]);
  async function loadReview(after:string|null=null,force=false){
    if(!validScope||(!force&&(busy||review.status==='loading')))return;
    const seq=++reviewSequence.current;
    const requested={...scope.current};
    setReview({...emptyReview(),status:'loading'});
    setDocument('');
    const params=new URLSearchParams({locationId:requested.loc,limit:'20'});
    if(after)params.set('afterDocumentId',after);
    try{
      const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(requested.org)+
        '/warehouse/supplier-returns/pending?'+params;
      const response=await fetch(path,{credentials:'include',cache:'no-store'});
      if(seq!==reviewSequence.current||
        scope.current.org!==requested.org||scope.current.loc!==requested.loc)return;
      if([401,403,404].includes(response.status)){
        setReview({...emptyReview(),status:'denied'});
        return;
      }
      if(!response.ok)throw Error('unavailable');
      const raw:unknown=await response.json();
      if(!raw||typeof raw!=='object')throw Error('invalid');
      const data=raw as {syntheticOnly?:unknown;items?:unknown;nextDocumentId?:unknown};
      if(data.syntheticOnly!==true||!Array.isArray(data.items)||
        data.items.length>20||
        !data.items.every((item:unknown)=>{
          if(!item||typeof item!=='object')return false;
          const row=item as Record<string,unknown>;
          return ['documentId','sourceReceiptId','lotId'].every(key=>
            typeof row[key]==='string'&&uuid.test(row[key] as string))&&
            ['article','batchCode','binCode','reason','supplierReference'].every(key=>
              typeof row[key]==='string'&&(row[key] as string).length>0&&
              (row[key] as string).length<=240)&&
            Number.isSafeInteger(row.quantity)&&Number(row.quantity)>=1&&
            Number(row.quantity)<=1000000000;
        })||!(data.nextDocumentId===null||
          (typeof data.nextDocumentId==='string'&&uuid.test(data.nextDocumentId))))
        throw Error('invalid');
      setReview({status:'ready',items:data.items as ReviewItem[],
        cursor:after,nextDocumentId:data.nextDocumentId as string|null});
    }catch{
      if(seq===reviewSequence.current&&scope.current.org===requested.org&&
        scope.current.loc===requested.loc)
        setReview({...emptyReview(),status:'error'});
    }
  }

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
      if(snap.org===scope.current.org&&snap.loc===scope.current.loc){
        if(snap.action==='draft')setDocument((data as Record<string,string>)[key]);
        else setApproval((data as Record<string,string>)[key]);
      }
      setPending(null);
      if(snap.action==='approve'){
        reviewSequence.current+=1;
        setReview(emptyReview());
        void loadReview(null,true);
      }
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
      <section aria-label="Очередь актов поставщику">
        <h3>Ожидающие утверждения акты возврата</h3>
        <p>Только MANAGER филиала. Проверьте артикул, партию,
          количество, причину и номер обращения поставщику.
          Собственные черновики не включаются: нужен другой сотрудник.</p>
        <button type="button" disabled={!validScope||busy||review.status==='loading'}
          onClick={()=>void loadReview()}>Обновить акты к утверждению</button>
        {review.status==='loading'&&<p role="status">Загрузка актов возврата…</p>}
        {review.status==='denied'&&<p role="alert">Нет доступа к актам возврата филиала.</p>}
        {review.status==='error'&&<p role="alert">Не удалось загрузить акты возврата.</p>}
        {review.status==='ready'&&(review.items.length?
          <ul>{review.items.map(item=><li key={item.documentId}>
            <strong>{item.article}</strong> · партия {item.batchCode} ·
            ячейка {item.binCode} · {item.quantity} шт.
            <p>Причина: {item.reason}; обращение: {item.supplierReference}</p>
            <button type="button" disabled={busy||!!pending}
              onClick={()=>setDocument(item.documentId)}>
              Выбрать акт {item.documentId}</button>
          </li>)}</ul>:<p>Актов к утверждению нет.</p>)}
        {review.status==='ready'&&review.nextDocumentId&&
          <button type="button" disabled={busy||!!pending}
            onClick={()=>void loadReview(review.nextDocumentId)}>
            Следующая страница актов</button>}
        {review.status==='ready'&&review.cursor&&
          <button type="button" disabled={busy||!!pending}
            onClick={()=>void loadReview()}>Первая страница актов</button>}
      </section>
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
