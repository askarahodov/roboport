import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Entry={assertionId:string;serialId:string;receiptId:string;approvalId:string|null;
  assertionSequence:number;approvalSequence:number|null};
type List={syntheticOnly:true;accountingApproved:false;items:Entry[]};
type Pending={url:string;body:string;key:string;action:'assert'|'approve'};

export function WarehouseSerialReceiptProvenance({organization,location,serial}:{
  organization:string;location:string;serial:string
}){
  const [receipt,setReceipt]=React.useState('');
  const [entries,setEntries]=React.useState<Entry[]>([]);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [loaded,setLoaded]=React.useState(false);
  const scopeOK=uuid.test(organization.trim())&&uuid.test(location.trim());
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(organization.trim())+
    '/locations/'+encodeURIComponent(location.trim())+'/serial-receipt-provenance';
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  async function load(){
    if(!scopeOK||busy||pending)return;
    active.current?.abort();
    const abort=new AbortController();active.current=abort;
    setBusy(true);setLoaded(false);setEntries([]);setMessage('');
    try{
      const r=await fetch(base,{credentials:'include',cache:'no-store',signal:abort.signal});
      if(!r.ok){setMessage([401,403,404].includes(r.status)?
        'Для проверки происхождения необходима роль руководителя и доступ к закупкам.':
        r.status===409?'Превышен лимит истории; сузьте область проверки.':'Список недоступен.');return;}
      const data=await r.json() as List;
      if(data.syntheticOnly!==true||data.accountingApproved!==false||
        !Array.isArray(data.items)||data.items.length>100||
        data.items.some(x=>!uuid.test(x.assertionId)||!uuid.test(x.serialId)||
          !uuid.test(x.receiptId)||(x.approvalId!==null&&!uuid.test(x.approvalId))||
          !Number.isSafeInteger(x.assertionSequence)||
          (x.approvalSequence!==null&&!Number.isSafeInteger(x.approvalSequence))))
        throw Error('contract');
      if(!abort.signal.aborted){setEntries(data.items);setLoaded(true);}
    }catch{if(!abort.signal.aborted)setMessage('Не удалось подтвердить перечень связей.');}
    finally{if(!abort.signal.aborted)setBusy(false);}
  }
  async function mutate(action:'assert'|'approve',entry?:Entry,retry?:Pending){
    if(!scopeOK||busy||(!retry&&!!pending)||(!retry&&action==='assert'&&
      (!uuid.test(serial.trim())||!uuid.test(receipt.trim()))))return;
    const key=retry?.key??crypto.randomUUID();
    const snap:Pending=retry??{action,key,url:action==='assert'?base:
      base+'/'+entry?.assertionId+'/approve',
      body:JSON.stringify(action==='assert'?{
        serialId:serial.trim(),receiptId:receipt.trim(),requestKey:key,
      }:{requestKey:key})};
    active.current?.abort();
    const abort=new AbortController();active.current=abort;
    setPending(snap);setBusy(true);setEntries([]);setLoaded(false);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',signal:abort.signal});
      if(!csrf.ok)throw Error('csrf');
      const token=await csrf.json() as {csrfToken?:string};
      if(typeof token.csrfToken!=='string')throw Error('csrf');
      const r=await fetch(snap.url,{method:'POST',credentials:'include',
        cache:'no-store',signal:abort.signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body:snap.body});
      if(abort.signal.aborted)return;
      if([401,403,404,409,422].includes(r.status)){
        setPending(null);
        setMessage([401,403,404].includes(r.status)?
          'Нет прав, требуется другой утверждающий либо связь не принадлежит филиалу.':
          r.status===409?'Конфликт истории, движения или ключа запроса.':'Неверные UUID или данные.');
        return;
      }
      if(!r.ok)throw Error('server');
      const data=await r.json() as {assertionId?:string;approvalId?:string;replayed?:boolean};
      if(data.replayed!==true&&data.replayed!==false||
        !uuid.test(action==='assert'?data.assertionId??'':data.approvalId??''))throw Error('reply');
      setPending(null);
      setMessage(action==='assert'?'Источник заявлен. Требуется независимое подтверждение руководителя.':
        'Источник подтверждён. При выдаче будет проверена закупочная оценка.');
    }catch{
      if(!abort.signal.aborted)setMessage('Результат операции неизвестен. Повторите с тем же ключом.');
    }finally{if(!abort.signal.aborted)setBusy(false);}
  }
  return <section aria-label="Происхождение серийных деталей">
    <h2>Происхождение серийной детали из прихода (dev)</h2>
    <p>Кладовщик связывает серийную деталь с принятым приходом; другой руководитель
      подтверждает источник до первого движения. Это учебное доказательство,
      не бухгалтерская сертификация. Старые выдачи не переоцениваются.</p>
    <label>UUID принятого прихода
      <input value={receipt} aria-label="UUID принятого прихода"
        disabled={busy||!!pending} onChange={e=>setReceipt(e.target.value)}/>
    </label>
    <button type="button" disabled={!scopeOK||!uuid.test(serial.trim())||
      !uuid.test(receipt.trim())||busy||!!pending}
      onClick={()=>void mutate('assert')}>Заявить источник серийной детали</button>
    <button type="button" disabled={!scopeOK||busy||!!pending} onClick={()=>void load()}>
      Проверить подтверждения филиала</button>
    {loaded&&<div>
      {!entries.length&&<p>Подтверждений ещё нет.</p>}
      <ul>{entries.map(e=><li key={e.assertionId}>
        Деталь: {e.serialId} · Приход: {e.receiptId} ·
        {e.approvalId?' Подтверждение: '+e.approvalId:' Ожидает независимого подтверждения'}
        {!e.approvalId&&<button type="button" disabled={busy||!!pending}
          onClick={()=>void mutate('approve',e)}>
            Подтвердить приход для {e.serialId}
          </button>}
      </li>)}</ul>
    </div>}
    {pending&&<button type="button" disabled={busy}
      onClick={()=>void mutate(pending.action,undefined,pending)}>
      Повторить запись с прежним ключом</button>}
    {busy&&<p role="status">Проверка происхождения…</p>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
