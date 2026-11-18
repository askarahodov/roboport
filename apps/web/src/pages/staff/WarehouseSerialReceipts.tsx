import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Item={assertionId:string;serialId:string;receiptId:string;locationId:string;
  assertedAt:string;approvalId:string|null;approvedAt:string|null;syntheticOnly:true};
type Pending={url:string;body:string;org:string;loc:string};
const valid=(raw:unknown,loc:string):Item[]=>{
  const x=raw as {syntheticOnly?:boolean;items?:Item[]};
  if(!x||x.syntheticOnly!==true||!Array.isArray(x.items)||x.items.length>200)throw Error('response');
  const seen=new Set<string>();
  for(const r of x.items){
    if(!r||![r.assertionId,r.serialId,r.receiptId,r.locationId].every(v=>
      typeof v==='string'&&uuid.test(v))||r.locationId!==loc||seen.has(r.serialId)||
      typeof r.assertedAt!=='string'||r.syntheticOnly!==true||
      (r.approvalId===null?r.approvedAt!==null:!uuid.test(r.approvalId||'')||
      typeof r.approvedAt!=='string'))throw Error('item');
    seen.add(r.serialId);
  }
  return x.items;
};

export function WarehouseSerialReceipts({org,loc}:{org:string;loc:string}){
  const [serial,setSerial]=React.useState('');
  const [receipt,setReceipt]=React.useState('');
  const [items,setItems]=React.useState<Item[]>([]);
  const [busy,setBusy]=React.useState(false);
  const [ready,setReady]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [error,setError]=React.useState('');
  const [success,setSuccess]=React.useState('');
  const abort=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>abort.current?.abort(),[]);
  React.useEffect(()=>{
    abort.current?.abort();setItems([]);setReady(false);setSuccess('');setError('');
  },[org,loc]);
  const active=uuid.test(org)&&uuid.test(loc);
  const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/warehouse/serial-receipts';
  async function run(url:string,body?:string){
    if(!active||busy)return;
    abort.current?.abort();
    const controller=new AbortController();abort.current=controller;
    setBusy(true);setError('');setSuccess('');
    setReady(false);setItems([]);
    if(body)setPending({url,body,org,loc});
    try{
      let headers:Record<string,string>|undefined;
      if(body){
        const csrf=await fetch('/api/v1/auth/csrf',{
          method:'POST',credentials:'include',cache:'no-store',signal:controller.signal});
        if(!csrf.ok)throw Error('csrf');
        const x=await csrf.json() as {csrfToken?:string};
        if(typeof x.csrfToken!=='string')throw Error('csrf');
        headers={'Content-Type':'application/json','X-CSRF-Token':x.csrfToken};
      }
      const response=await fetch(url,{method:body?'POST':'GET',body,headers,
        credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404,409,422].includes(response.status)){
        if(body)setPending(null);
        setError([401,403,404].includes(response.status)?
          'Нет доступа к филиалу или требуется другой руководитель для подтверждения.':
          response.status===409?'Состояние детали изменилось или запрос уже использован (409).':
          'Неверные UUID или параметры заявления (422).');return;
      }
      if(!response.ok)throw Error('http');
      if(body){
        const item=await response.json() as Item;
        if(!item||!uuid.test(item.assertionId)||!uuid.test(item.serialId)||
          !uuid.test(item.receiptId)||item.locationId!==loc)throw Error('item');
        setPending(null);
        setSuccess('Операция подтверждена. Перечитайте журнал для проверки статуса.');
      }else{
        const data=valid(await response.json(),loc);
        setItems(data);setReady(true);
      }
    }catch{
      if(!controller.signal.aborted)setError(body?
        'Результат записи неизвестен. Повторите тот же запрос и ключ или перечитайте журнал.':
        'Не удалось загрузить журнал серийной первички.');
    }finally{if(!controller.signal.aborted)setBusy(false);}
  }
  return <section aria-label="Привязка серийных деталей к приходам">
    <h2>Серийные детали: первичка прихода (dev)</h2>
    <p>Кладовщик связывает одну неподвижную серийную деталь с независимой принятой поставкой.
      Другой действующий руководитель подтверждает источник до первой выдачи или перемещения.
      Закупочные цены здесь не отображаются. Задним числом привязка запрещена.</p>
    <label>UUID серийной детали для первички
      <input aria-label="UUID серийной детали для первички"
        value={serial} disabled={busy||!!pending} onChange={e=>setSerial(e.target.value)}/>
    </label>
    <label>UUID принятого прихода
      <input aria-label="UUID принятого прихода"
        value={receipt} disabled={busy||!!pending} onChange={e=>setReceipt(e.target.value)}/>
    </label>
    <button type="button" disabled={!active||busy||!!pending||!uuid.test(serial)||!uuid.test(receipt)}
      onClick={()=>void run(path,JSON.stringify({serialId:serial,receiptId:receipt,
        requestKey:crypto.randomUUID()}))}>Связать серию с приходом</button>
    <button type="button" disabled={!active||busy||!!pending}
      onClick={()=>void run(path)}>Прочитать подтверждения серийных деталей</button>
    {ready&&!items.length&&<p>Нет привязок к приходам для филиала.</p>}
    {ready&&items.length>0&&<ul>{items.map(x=><li key={x.assertionId}>
      <p>Серия: {x.serialId} · Приход: {x.receiptId} ·
        {x.approvalId?'Подтверждено':'Ожидает независимого утверждения'}</p>
      {!x.approvalId&&<button type="button" disabled={busy||!!pending}
        onClick={()=>void run(path+'/'+x.assertionId+'/approve',
          JSON.stringify({requestKey:crypto.randomUUID()}))}>
        Утвердить привязку {x.assertionId}
      </button>}
    </li>)}</ul>}
    {busy&&<p role="status">Проверка первички…</p>}
    {error&&<p role="alert">{error}</p>}
    {success&&<p role="status">{success}</p>}
    {pending&&<button type="button" disabled={busy||pending.org!==org||pending.loc!==loc}
      onClick={()=>void run(pending.url,pending.body)}>Повторить запись с тем же ключом</button>}
  </section>;
}
