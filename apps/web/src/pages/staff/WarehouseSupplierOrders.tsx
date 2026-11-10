import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Quote={supplier:string;offerId:string;unit:string;available:string;days:string;revision:string};
type Order={orderId:string;workOrderId:string;lotId:string;article:string;
  orderedQuantity:number;supplierId:string;offerId:string;quoteRevision:string;
  purchaseUnitMinor:number|null;purchasePriceHidden?:boolean;
  currency:string;documentedQuantity:number;
  acceptedQuantity:number;valuedQuantity:number;canceledQuantity:number;
  outstandingQuantity:number};
type Pending={kind:'order'|'delivery'|'cancel';path:string;body:string;
  key:string;org:string;loc:string};
const initialQuote=(n:number):Quote=>({
  supplier:'SYNTH-SUP-'+(n+1),offerId:'OFFLINE-OFFER-'+(n+1),
  unit:n===0?'1200':'1350',available:'10',days:n===0?'2':'1',
  revision:'SYNTHETIC-QUOTE-'+(n+1),
});
function validQty(s:string,max=1000000000){
  return /^(0|[1-9][0-9]*)$/.test(s)&&Number.isSafeInteger(Number(s))&&
    Number(s)>=1&&Number(s)<=max;
}

export function WarehouseSupplierOrders(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [workOrder,setWorkOrder]=React.useState('');
  const [lot,setLot]=React.useState('');
  const [article,setArticle]=React.useState('');
  const [desired,setDesired]=React.useState('5');
  const [quotes,setQuotes]=React.useState<[Quote,Quote]>([initialQuote(0),initialQuote(1)]);
  const [chosen,setChosen]=React.useState(0);
  const [selectedOrder,setSelectedOrder]=React.useState('');
  const [receiptQty,setReceiptQty]=React.useState('1');
  const [reference,setReference]=React.useState('SYNTH-DELIVERY-1');
  const [cancelConfirmed,setCancelConfirmed]=React.useState(false);
  const [rows,setRows]=React.useState<Order[]>([]);
  const [next,setNext]=React.useState<string|null>(null);
  const [listStatus,setListStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const context=React.useRef({org:'',loc:''});
  context.current={org:org.trim(),loc:loc.trim()};
  const requestSeq=React.useRef(0);
  React.useEffect(()=>{
    requestSeq.current+=1;setRows([]);setNext(null);setSelectedOrder('');
    setListStatus('idle');setPending(null);setMessage('');setCancelConfirmed(false);
  },[org,loc]);
  const scopeOk=uuid.test(org.trim())&&uuid.test(loc.trim());
  const quoteOk=quotes.every(q=>q.supplier.trim().length>0&&
    q.supplier.length<=160&&q.offerId.trim().length>0&&q.offerId.length<=160&&
    validQty(q.available)&&validQty(q.unit,1000000000)&&
    /^(0|[1-9][0-9]*)$/.test(q.days)&&Number(q.days)<=365&&
    q.revision.trim().length>=1&&q.revision.length<=160)&&
    quotes[0].supplier!==quotes[1].supplier;
  const orderOk=scopeOk&&uuid.test(workOrder.trim())&&uuid.test(lot.trim())&&
    article.trim().length>0&&article.trim().length<=80&&
    validQty(desired)&&quoteOk;
  const root=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/warehouse/supplier-orders';
  const chosenRow=rows.find(x=>x.orderId===selectedOrder);
  async function load(after:string|null=null){
    if(!scopeOk||busy||listStatus==='loading')return;
    const seq=++requestSeq.current;
    const scope={...context.current};setRows([]);setNext(null);
    setListStatus('loading');setSelectedOrder('');setMessage('');
    try{
      const query=new URLSearchParams({locationId:scope.loc,limit:'20'});
      if(after)query.set('afterOrderId',after);
      const response=await fetch(root()+'?'+query.toString(),{
        credentials:'include',cache:'no-store',
      });
      if(seq!==requestSeq.current||scope.org!==context.current.org||scope.loc!==context.current.loc)return;
      if([401,403,404].includes(response.status)){setListStatus('denied');return;}
      if(!response.ok)throw Error('http');
      const payload:unknown=await response.json();
      if(!payload||typeof payload!=='object')throw Error('body');
      const data=payload as {syntheticOnly?:unknown;items?:unknown;nextOrderId?:unknown};
      if(data.syntheticOnly!==true||!Array.isArray(data.items)||data.items.length>20||
         !(data.nextOrderId===null||(typeof data.nextOrderId==='string'&&uuid.test(data.nextOrderId)))||
         !data.items.every((raw:unknown)=>{
           if(!raw||typeof raw!=='object')return false;
           const row=raw as Record<string,unknown>;
           return ['orderId','workOrderId','lotId'].every(k=>
             typeof row[k]==='string'&&uuid.test(String(row[k])))&&
             ['orderedQuantity','documentedQuantity','acceptedQuantity',
              'valuedQuantity','canceledQuantity','outstandingQuantity']
               .every(k=>typeof row[k]==='number'&&Number.isSafeInteger(row[k])&&
                 Number(row[k])>=0)&&
             (typeof row.purchaseUnitMinor==='number'&&
               Number.isSafeInteger(row.purchaseUnitMinor)&&
               row.purchaseUnitMinor>=0||
               row.purchaseUnitMinor===null&&row.purchasePriceHidden===true)&&
             row.currency==='RUB';
         }))throw Error('invalid list');
      setRows(data.items as Order[]);
      setNext(data.nextOrderId as string|null);
      setListStatus('ready');
    }catch{
      if(seq===requestSeq.current&&scope.org===context.current.org&&scope.loc===context.current.loc)
        setListStatus('error');
    }
  }
  const updateQuote=(index:0|1,field:keyof Quote,value:string)=>{
    setQuotes(prev=>prev.map((q,i)=>i===index?{...q,[field]:value}:q) as [Quote,Quote]);
  };
  function orderPayload(){
    const quotedAt=new Date();
    const expiry=new Date(quotedAt.getTime()+7*86400000);
    const offers=quotes.map(q=>({
      supplier_id:q.supplier.trim(),offer_id:q.offerId.trim(),source_revision:q.revision.trim(),
      quoted_at:quotedAt.toISOString(),expires_at:expiry.toISOString(),
      currency:'RUB',unit_purchase_minor:Number(q.unit),
      available_quantity:Number(q.available),article:article.trim(),
      delivery_days:Number(q.days),
    }));
    return {locationId:loc.trim(),workOrderId:workOrder.trim(),lotId:lot.trim(),
      desiredQuantity:Number(desired),offers,
      selectedSupplierId:offers[chosen].supplier_id,selectedOfferId:offers[chosen].offer_id};
  }
  async function submit(kind:Pending['kind'],retry?:Pending){
    if(busy||(!retry&&pending))return;
    if(!retry&&(
      kind==='order'?!orderOk:
      kind==='delivery'?!(scopeOk&&uuid.test(selectedOrder)&&validQty(receiptQty)&&
        reference.trim().length>=3&&reference.trim().length<=160&&
        !!chosenRow&&Number(receiptQty)<=chosenRow.outstandingQuantity):
      !(scopeOk&&uuid.test(selectedOrder)&&cancelConfirmed&&
        !!chosenRow&&chosenRow.outstandingQuantity>0)
    ))return;
    const snap=retry??{
      kind,org:org.trim(),loc:loc.trim(),
      path:kind==='order'?root():root()+'/'+selectedOrder+
        (kind==='delivery'?'/receipts':'/cancel'),
      key:crypto.randomUUID(),
      body:JSON.stringify(kind==='order'?orderPayload():kind==='delivery'?{
        locationId:loc.trim(),quantity:Number(receiptQty),supplierReference:reference.trim(),
      }:{locationId:loc.trim()}),
    };
    if(snap.org!==context.current.org||snap.loc!==context.current.loc)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if(!csrf.ok)throw Error('session');
      const c:unknown=await csrf.json();
      if(!c||typeof c!=='object'||!('csrfToken' in c)||
         typeof c.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(snap.path,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':c.csrfToken,
          'Idempotency-Key':snap.key},
        body:snap.body,
      });
      if(snap.org!==context.current.org||snap.loc!==context.current.loc)return;
      if(response.status>=500)throw Error('unknown');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?'409 — конфликт количества, цены или состояния заказа. Перечитайте список.':
          [401,403,404].includes(response.status)?'Нет права WAREHOUSE, истёк доступ или объект скрыт.':
          '422 — проверьте поля и предложение поставщика.');
        return;
      }
      const data:unknown=await response.json();
      if(!data||typeof data!=='object'||!("syntheticOnly" in data)||
         (data as {syntheticOnly:unknown}).syntheticOnly!==true)
        throw Error('invalid response');
      const d=data as Record<string,unknown>;
      const key=snap.kind==='order'?'orderId':snap.kind==='delivery'?'documentId':'cancelId';
      if(typeof d[key]!=='string'||!uuid.test(d[key] as string))throw Error('invalid id');
      setPending(null);setCancelConfirmed(false);
      if(snap.kind==='order')setSelectedOrder(d.orderId as string);
      setMessage(snap.kind==='order'?
        'Заказ создан. Дефицит заказан; физический остаток не изменился.':
        snap.kind==='delivery'?
        'Частичный приход зарегистрирован как черновик. Другой MANAGER отдельно подтвердит приёмку и закупочную стоимость. Документ: '+d.documentId:
        'Непоставленный остаток заказа отменён; уже созданные приходные документы сохранены.');
      // Existing list may now be stale; clear it until a deliberate fresh fetch.
      requestSeq.current+=1;setRows([]);setNext(null);setListStatus('idle');
    }catch{
      if(snap.org===context.current.org&&snap.loc===context.current.loc){
        setPending(snap);
        setMessage('Неизвестный результат запроса. Повторите с прежним ключом, чтобы не дублировать поставку.');
      }
    }finally{setBusy(false);}
  }
  return <section className="ui-page">
    <h1>Заказы поставщикам (dev)</h1>
    <p>БоксРитм · только синтетические WorkOrder, SKU и предложения. Ни денег, ни заказов реальному поставщику не отправляется.</p>
    <p><a href="/docs/#/guides/warehouse">Руководство кладовщика</a> ·
      <a href="/docs/#/guides/STAFF_TASKS">Карта операций</a></p>
    <div className="auto-claim-forms">
      <label>UUID организации<input value={org} onChange={e=>setOrg(e.target.value)} /></label>
      <label>UUID филиала<input value={loc} onChange={e=>setLoc(e.target.value)} /></label>
    </div>
    <section aria-label="Дефицит и предложения">
      <h2>1. Дефицит заказ-наряда</h2>
      <div className="auto-claim-forms">
        <label>UUID заказ-наряда<input value={workOrder} onChange={e=>setWorkOrder(e.target.value)}/></label>
        <label>UUID партии<input value={lot} onChange={e=>setLot(e.target.value)}/></label>
        <label>Артикул SKU<input value={article} onChange={e=>setArticle(e.target.value)}/></label>
        <label>Требуется для работы (шт.)<input type="number" min="1" max="1000000000"
          value={desired} onChange={e=>setDesired(e.target.value)}/></label>
      </div>
      <p>Сервер рассчитывает дефицит как требуемое количество минус свободный остаток; заказа без дефицита нет. Подтверждение применяемости детали здесь не производится.</p>
      <h2>2. Сравнение двух предложений (offline fixture)</h2>
      <div className="auto-claim-forms">
        {quotes.map((q,i)=><fieldset key={i} style={{minWidth:0,border:'1px solid var(--rp-border)',borderRadius:12,padding:12}}>
          <legend>Предложение {i+1}</legend>
          <label>Поставщик (synthetic)<input value={q.supplier} onChange={e=>updateQuote(i as 0|1,'supplier',e.target.value)}/></label>
          <label>ID предложения<input value={q.offerId} onChange={e=>updateQuote(i as 0|1,'offerId',e.target.value)}/></label>
          <label>Закупочная цена за штуку (коп.)<input type="number" min="0" value={q.unit} onChange={e=>updateQuote(i as 0|1,'unit',e.target.value)}/></label>
          <label>Доступное количество<input type="number" min="1" value={q.available} onChange={e=>updateQuote(i as 0|1,'available',e.target.value)}/></label>
          <label>Плановый срок (дни, не SLA)<input type="number" min="0" max="365" value={q.days} onChange={e=>updateQuote(i as 0|1,'days',e.target.value)}/></label>
          <label>Ревизия цены<input value={q.revision} onChange={e=>updateQuote(i as 0|1,'revision',e.target.value)}/></label>
          <label><input type="radio" name="supplier-choice" checked={chosen===i}
            onChange={()=>setChosen(i)}/> Выбрать предложение {i+1}</label>
        </fieldset>)}
      </div>
      <p>Сравните цену, доступность и обещанный срок; все исходные данные ниже — вручную введённые **непроверенные** синтетические предложения, а не договор и не лицензированный OEM-каталог.</p>
      <button disabled={!orderOk||!!pending||busy} onClick={()=>void submit('order')}>Создать заказ поставщику</button>
    </section>
    <section aria-label="Список заказов поставщикам">
      <h2>3. Частичная поставка и отмена остатка</h2>
      <button disabled={!scopeOk||busy||!!pending||listStatus==='loading'} onClick={()=>void load()}>Обновить заказы</button>
      {listStatus==='loading'&&<p role="status">Загрузка заказов…</p>}
      {listStatus==='denied'&&<p role="alert">403/404 — заказ поставщика другого филиала недоступен.</p>}
      {listStatus==='error'&&<p role="alert">Список недоступен. Повторите запрос после проверки сети.</p>}
      {listStatus==='ready'&&rows.length===0&&<p>Нет заказов в выбранном филиале.</p>}
      {rows.length>0&&<ul>{rows.map(row=><li key={row.orderId}>
        <button aria-pressed={selectedOrder===row.orderId} onClick={()=>{setSelectedOrder(row.orderId);setCancelConfirmed(false);}}>
          Выбрать заказ {row.orderId}</button>
        <p>{row.article}: {row.supplierId}, предложение {row.offerId}, закупочная цена {row.purchaseUnitMinor===null?'скрыта настройкой доступа':
          row.purchaseUnitMinor+' коп./шт. (RUB)'}.</p>
        <p>Заказано {row.orderedQuantity}; документы {row.documentedQuantity}; принято {row.acceptedQuantity};
          оценено {row.valuedQuantity}; отменено {row.canceledQuantity}; к поставке {row.outstandingQuantity}.</p>
      </li>)}</ul>}
      {next&&<button disabled={busy||!!pending} onClick={()=>void load(next)}>Следующая страница заказов</button>}
      <p>После создания приходного документа перейдите в <a href="/staff/warehouse-receiving">«Приход запчастей (dev)»</a>:
        другой MANAGER подтверждает документ и фиксирует закупочную оценку; до этого фактический остаток не меняется.</p>
      <label>Количество текущей поставки<input type="number" min="1" value={receiptQty} onChange={e=>setReceiptQty(e.target.value)}/></label>
      <label>Номер документа поставки<input value={reference} onChange={e=>setReference(e.target.value)}/></label>
      <button disabled={!chosenRow||chosenRow.outstandingQuantity<Number(receiptQty)||
        !validQty(receiptQty)||reference.trim().length<3||!!pending||busy}
        onClick={()=>void submit('delivery')}>Принять частичную поставку в черновик</button>
      <label><input type="checkbox" checked={cancelConfirmed} onChange={e=>setCancelConfirmed(e.target.checked)}/>
        Подтверждаю отмену только непоставленного остатка заказа</label>
      <button disabled={!chosenRow||!cancelConfirmed||chosenRow.outstandingQuantity<1||!!pending||busy}
        onClick={()=>void submit('cancel')}>Отменить непоставленный остаток</button>
    </section>
    {pending&&<div role="alert">
      Неизвестный результат {pending.kind}. Менять данные запроса нельзя.
      <button disabled={busy} onClick={()=>void submit(pending.kind,pending)}>Повторить с прежним ключом</button>
    </div>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
