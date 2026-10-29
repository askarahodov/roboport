import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Kind='draft'|'accept'|'valuation';
type Pending={kind:Kind;path:string;key:string;body:string;org:string;location:string};
type Item={lotId:string;article:string;batchCode:string;binCode:string;allocated:number;onHand:number;reserved:number};

export function WarehouseReceiving(){
  const [org,setOrg]=React.useState('');
  const [location,setLocation]=React.useState('');
  const [lot,setLot]=React.useState('');
  const [quantity,setQuantity]=React.useState('1');
  const [reference,setReference]=React.useState('');
  const [documentId,setDocumentId]=React.useState('');
  const [acceptanceId,setAcceptanceId]=React.useState('');
  const [valuationId,setValuationId]=React.useState('');
  const [currency,setCurrency]=React.useState('RUB');
  const [unitCost,setUnitCost]=React.useState('0');
  const [vatTotal,setVatTotal]=React.useState('0');
  const [priceRevision,setPriceRevision]=React.useState('');
  const [quotedAt,setQuotedAt]=React.useState(()=>new Date().toISOString());
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [lots,setLots]=React.useState<Item[]>([]);
  const [listState,setListState]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const context=React.useRef({org:'',location:''});
  context.current={org:org.trim(),location:location.trim()};
  React.useEffect(()=>{setPending(null);setMessage('');setLots([]);
    setListState('idle');setDocumentId('');setAcceptanceId('');setValuationId('');},[org,location]);
  const validScope=uuid.test(org.trim())&&uuid.test(location.trim());
  const validDraft=validScope&&uuid.test(lot.trim())&&
    /^(0|[1-9][0-9]*)$/.test(quantity)&&Number(quantity)>0&&
    Number(quantity)<=1_000_000_000&&reference.trim().length>=3&&reference.trim().length<=160;
  const validValuation=validScope&&uuid.test(documentId.trim())&&
    /^[A-Z]{3}$/.test(currency)&&/^(0|[1-9][0-9]*)$/.test(unitCost)&&
    /^(0|[1-9][0-9]*)$/.test(vatTotal)&&Number(unitCost)<=1_000_000_000&&
    Number.isSafeInteger(Number(unitCost))&&Number.isSafeInteger(Number(vatTotal))&&
    Number(vatTotal)<=1_000_000_000_000_000_000&&
    priceRevision.trim().length>=3&&priceRevision.trim().length<=160&&
    /^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?(?:Z|[+-]\\d{2}:\\d{2})$/.test(quotedAt)&&
    !Number.isNaN(Date.parse(quotedAt));
  async function getLots(){
    if(!validScope||listState==='loading')return;
    const scope={...context.current};
    setLots([]);setListState('loading');
    try{
      const params=new URLSearchParams({locationId:scope.location,limit:'30'});
      const response=await fetch('/api/v1/dev/auto/organizations/'+scope.org+
        '/stocktake/lots?'+params.toString(),{credentials:'include',cache:'no-store'});
      if(scope.org!==context.current.org||scope.location!==context.current.location)return;
      if([401,403,404].includes(response.status)){setListState('denied');return;}
      if(!response.ok)throw new Error('list');
      const payload:unknown=await response.json();
      if(!payload||typeof payload!=='object'||!('items' in payload)||
          !Array.isArray(payload.items)||payload.items.length>30||
          !payload.items.every((row:unknown)=>row&&typeof row==='object'&&
            'lotId' in row&&typeof row.lotId==='string'&&uuid.test(row.lotId)&&
            'batchCode' in row&&typeof row.batchCode==='string'&&
            'article' in row&&typeof row.article==='string'&&
            'binCode' in row&&typeof row.binCode==='string'&&
            ['allocated','onHand','reserved'].every(k=>k in row&&
              typeof (row as Record<string,unknown>)[k]==='number'&&
              Number.isSafeInteger((row as Record<string,number>)[k])&&
              (row as Record<string,number>)[k]>=0)))
        throw new Error('invalid list');
      setLots(payload.items as Item[]);setListState('ready');
    }catch{if(scope.org===context.current.org&&scope.location===context.current.location)
      setListState('error');}
  }
  async function submit(kind:Kind,retry?:Pending){
    if(busy||(!retry&&pending))return;
    if(!retry&&!(kind==='draft'?validDraft:kind==='accept'?
      validScope&&uuid.test(documentId.trim()):validValuation))return;
    const snap=retry??{
      kind,org:org.trim(),location:location.trim(),
      path:'/api/v1/dev/auto/organizations/'+org.trim()+'/warehouse/receipts'+
        (kind==='accept'?'/accept':kind==='valuation'?'/valuation':''),
      key:crypto.randomUUID(),
      body:JSON.stringify(kind==='draft'?{
        locationId:location.trim(),lotId:lot.trim(),
        quantity:Number(quantity),sourceReference:reference.trim(),
      }:kind==='accept'?{locationId:location.trim(),documentId:documentId.trim()}:
      {locationId:location.trim(),documentId:documentId.trim(),currency,
       unitNetMinor:Number(unitCost),vatTotalMinor:Number(vatTotal),
       supplierPriceRevision:priceRevision.trim(),supplierQuotedAt:quotedAt}),
    };
    if(snap.org!==context.current.org||snap.location!==context.current.location)return;
    setBusy(true);setMessage('');
    try{
      const responseCsrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store'});
      if(!responseCsrf.ok)throw new Error('csrf');
      const csrf:unknown=await responseCsrf.json();
      if(!csrf||typeof csrf!=='object'||!('csrfToken' in csrf)||
        typeof csrf.csrfToken!=='string')throw new Error('csrf');
      const response=await fetch(snap.path,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrfToken,
          'Idempotency-Key':snap.key},body:snap.body,
      });
      if(snap.org!==context.current.org||snap.location!==context.current.location)return;
      if(response.status>=500)throw new Error('uncertain');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?'Конфликт документа или повторная приёмка (409).':
          [401,403,404].includes(response.status)?'Нет прав WAREHOUSE/MANAGER или доступ отозван.':
          'Неверные данные ('+response.status+').');
        return;
      }
      const body:unknown=await response.json();
      const field=snap.kind==='draft'?'documentId':snap.kind==='accept'?'acceptanceId':'valuationId';
      if(!body||typeof body!=='object'||!(field in body)||
          typeof (body as Record<string,unknown>)[field]!=='string'||
          !uuid.test((body as Record<string,string>)[field]))throw new Error('invalid response');
      if(snap.kind==='draft')setDocumentId((body as Record<string,string>)[field]);
      else if(snap.kind==='accept')setAcceptanceId((body as Record<string,string>)[field]);
      else setValuationId((body as Record<string,string>)[field]);
      setPending(null);
      setMessage(snap.kind==='draft'?'Документ создан. Остатки пока не изменены.':
        snap.kind==='accept'?'Приёмка подтверждена другим сотрудником. Остатки обновлены.':
        'Закупочная стоимость и НДС зафиксированы отдельно от остатков и цены продажи.');
    }catch{
      if(snap.org===context.current.org&&snap.location===context.current.location){
        setPending(snap);
        setMessage('Неизвестный результат. Повторите запрос с тем же ключом.');
      }
    }finally{setBusy(false);}
  }
  return <section className="ui-page" aria-label="Приход запчастей">
    <h1>Приход запчастей (dev)</h1>
    <p>Только синтетический склад. Черновик — кладовщик WAREHOUSE; подтверждение — другой MANAGER.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={location}
      onChange={e=>setLocation(e.target.value)}/></label>
    <section aria-label="Черновик приходного документа">
      <h2>1. Создать приходный документ</h2>
      <button type="button" disabled={!validScope||listState==='loading'}
        onClick={()=>void getLots()}>Показать партии</button>
      {listState==='loading'&&<p role="status">Загрузка партий…</p>}
      {listState==='denied'&&<p role="alert">Нет доступа к партиям филиала.</p>}
      {listState==='error'&&<p role="alert">Ошибка загрузки партий.</p>}
      {listState==='ready'&&(lots.length?<ul>{lots.map(row=><li key={row.lotId}>
        {row.article} · партия {row.batchCode} · ячейка {row.binCode} · остаток {row.allocated}
        <button type="button" onClick={()=>setLot(row.lotId)}>Выбрать {row.batchCode}</button>
      </li>)}</ul>:<p>Партий нет.</p>)}
      <label>UUID партии <input aria-label="UUID партии" value={lot} onChange={e=>setLot(e.target.value)}/></label>
      <label>Количество к приёмке <input type="number" min="1" max="1000000000" step="1"
        aria-label="Количество к приёмке" value={quantity} onChange={e=>setQuantity(e.target.value)}/></label>
      <label>Номер документа поставки <input aria-label="Номер документа поставки"
        maxLength={160} value={reference} onChange={e=>setReference(e.target.value)}/></label>
      <button type="button" disabled={!validDraft||busy||!!pending}
        onClick={()=>void submit('draft')}>Создать документ</button>
    </section>
    <section aria-label="Подтвердить приёмку">
      <h2>2. Подтвердить другим сотрудником</h2>
      <p>Используйте отдельную сессию MANAGER. Самостоятельное подтверждение не допускается.</p>
      <label>UUID приходного документа <input aria-label="UUID приходного документа"
        value={documentId} onChange={e=>setDocumentId(e.target.value)}/></label>
      <button type="button" disabled={!validScope||!uuid.test(documentId.trim())||busy||!!pending}
        onClick={()=>void submit('accept')}>Подтвердить приёмку</button>
    </section>
    <section aria-label="Закупочная стоимость и НДС">
      <h2>3. Зафиксировать закупочную стоимость (MANAGER)</h2>
      <p>Суммы вводятся в минимальных единицах валюты, НДС отдельно. Это не цена продажи и не расчёт налоговой ставки.</p>
      <label>Валюта ISO <input aria-label="Валюта ISO" maxLength={3} value={currency}
        onChange={e=>setCurrency(e.target.value.toUpperCase())}/></label>
      <label>Закупочная цена за единицу (minor units) <input type="number" min="0" max="1000000000" step="1"
        aria-label="Закупочная цена за единицу" value={unitCost} onChange={e=>setUnitCost(e.target.value)}/></label>
      <label>НДС по документу (minor units) <input type="number" min="0" step="1"
        aria-label="НДС по документу" value={vatTotal} onChange={e=>setVatTotal(e.target.value)}/></label>
      <label>Ревизия цены поставщика <input aria-label="Ревизия цены поставщика" maxLength={160}
        value={priceRevision} onChange={e=>setPriceRevision(e.target.value)}/></label>
      <label>Время цены поставщика ISO UTC <input aria-label="Время цены поставщика ISO UTC"
        value={quotedAt} onChange={e=>setQuotedAt(e.target.value)}/></label>
      <button type="button" disabled={!validValuation||busy||!!pending}
        onClick={()=>void submit('valuation')}>Сохранить закупочную стоимость</button>
    </section>
    {pending&&<button type="button" disabled={busy}
      onClick={()=>void submit(pending.kind,pending)}>Повторить с прежним ключом</button>}
    {message&&<p role="status">{message}</p>}
    {acceptanceId&&<p>Идентификатор подтверждения: <code>{acceptanceId}</code></p>}
    {valuationId&&<p>Идентификатор оценки: <code>{valuationId}</code></p>}}
  </section>;
}
