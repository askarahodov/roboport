import React from 'react';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const id=(x:unknown):x is string=>typeof x==='string'&&uuid.test(x);
const integer=(x:unknown):x is string=>typeof x==='string'&&/^(0|[1-9][0-9]{0,18})$/.test(x);
const cursor=(x:unknown):x is string|null=>x===null||typeof x==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(x);
const labels={PENDING:'Ожидает решения',APPROVED:'Утверждён',REJECTED:'Отклонён',CANCELED:'Отменён'} as const;
const reasons={DAMAGED:'Повреждение',EXPIRED:'Истёк срок хранения',UNUSABLE:'Непригодность',OTHER:'Другая причина'} as const;
type Status=keyof typeof labels;
type Reason=keyof typeof reasons;
type Action='APPROVE'|'REJECT'|'CANCEL';
type Lot={lotId:string;article:string;batchCode:string;binCode:string;available:string};
type Decision={id:string;note:string;actorId:string;createdAt:string;dispositionId:string|null;stockEventId:string|null;lotDebitId:string|null};
type Entry=Omit<Lot,'available'>&{documentId:string;quantity:string;reasonCode:Reason;note:string;createdBy:string;createdAt:string;
  status:Status;canReview:boolean;canCancel:boolean;decision:Decision|null};
type Snapshot={syntheticOnly:true;organizationId:string;locationId:string;canDraft:boolean;items:Entry[];lots:Lot[];nextCursor:string|null;nextLotCursor:string|null};
type Pending={path:string;key:string;body:string;field:'documentId'|'decisionId'};
type Pages={cursor?:string;lotCursor?:string};
const stamp=(x:unknown)=>typeof x==='string'&&Number.isFinite(Date.parse(x));
const noteOk=(x:string)=>x===x.trim()&&x.length>=3&&x.length<=240&&!/[\x00-\x1f]/.test(x);
function parse(raw:unknown,org:string,loc:string):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const d=raw as Snapshot;
  const lotOk=(x:Lot|Entry)=>x&&id(x.lotId)&&typeof x.article==='string'&&typeof x.batchCode==='string'&&typeof x.binCode==='string';
  if(d.syntheticOnly!==true||d.organizationId!==org.toLowerCase()||d.locationId!==loc.toLowerCase()||typeof d.canDraft!=='boolean'||
    !cursor(d.nextCursor)||!cursor(d.nextLotCursor)||!Array.isArray(d.lots)||d.lots.length>50||
    (!d.canDraft&&(d.lots.length>0||d.nextLotCursor!==null))||(d.nextLotCursor!==null&&d.lots.length!==50)||
    !d.lots.every(x=>lotOk(x)&&integer(x.available))||new Set(d.lots.map(x=>x.lotId)).size!==d.lots.length||
    !Array.isArray(d.items)||d.items.length>50||(d.nextCursor!==null&&d.items.length!==50)||
    !d.items.every(x=>lotOk(x)&&id(x.documentId)&&integer(x.quantity)&&BigInt(x.quantity)>0n&&BigInt(x.quantity)<=1000000000n&&
      Object.hasOwn(reasons,x.reasonCode)&&typeof x.note==='string'&&id(x.createdBy)&&stamp(x.createdAt)&&
      Object.hasOwn(labels,x.status)&&typeof x.canReview==='boolean'&&typeof x.canCancel==='boolean'&&!(x.canReview&&x.canCancel)&&
      (x.status==='PENDING'?x.decision===null:!x.canReview&&!x.canCancel&&!!x.decision&&id(x.decision.id)&&
        id(x.decision.actorId)&&stamp(x.decision.createdAt)&&typeof x.decision.note==='string'&&
        (x.status==='APPROVED'?[x.decision.dispositionId,x.decision.stockEventId,x.decision.lotDebitId].every(id):
          [x.decision.dispositionId,x.decision.stockEventId,x.decision.lotDebitId].every(v=>v===null))))||
    new Set(d.items.map(x=>x.documentId)).size!==d.items.length)throw Error('shape');
  return d;
}
export function WarehouseWriteoffs(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [query,setQuery]=React.useState('');
  const [status,setStatus]=React.useState('ALL');
  const [pages,setPages]=React.useState<Pages>({});
  const [data,setData]=React.useState<Snapshot|null>(null);
  const [lot,setLot]=React.useState('');
  const [qty,setQty]=React.useState('1');
  const [reason,setReason]=React.useState<Reason>('DAMAGED');
  const [note,setNote]=React.useState('');
  const [decision,setDecision]=React.useState<{id:string;action:Action}|null>(null);
  const [decisionNote,setDecisionNote]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [denied,setDenied]=React.useState(false);
  const op=React.useRef<AbortController|null>(null);
  const validScope=id(org.trim())&&id(loc.trim());
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/warehouse/writeoffs';
  const selected=data?.lots.find(x=>x.lotId===lot);
  const canDraft=!!data?.canDraft&&!!selected&&integer(qty)&&BigInt(qty)>0n&&BigInt(qty)<=1000000000n&&BigInt(qty)<=BigInt(selected.available)&&noteOk(note);
  const target=data?.items.find(x=>x.documentId===decision?.id);
  const canDecide=!!target&&!!decision&&(decision.action==='CANCEL'?target.canCancel:target.canReview)&&noteOk(decisionNote);
  function clear(){
    op.current?.abort();op.current=null;setData(null);setPages({});setLot('');setQty('1');setNote('');
    setDecision(null);setDecisionNote('');setPending(null);setBusy(false);setMessage('');setDenied(false);
  }
  function deny(){clear();setDenied(true);}
  React.useEffect(()=>()=>op.current?.abort(),[]);
  async function load(next:Pages={}){
    if(!validScope||op.current||pending)return;
    const c=new AbortController();op.current=c;setBusy(true);setDenied(false);
    setDecision(null);setDecisionNote('');
    if(!next.cursor&&!next.lotCursor){setData(null);setLot('');}
    try{
      const params=new URLSearchParams({locationId:loc.trim(),q:query.trim(),status});
      for(const [k,v] of Object.entries(next))if(v)params.set(k,v);
      const res=await fetch(base+'?'+params,{credentials:'include',cache:'no-store',signal:c.signal});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){deny();return;}
      if(!res.ok)throw Error('read');
      const parsed=parse(await res.json(),org.trim(),loc.trim());
      if(c.signal.aborted)return;
      if((parsed.nextCursor&&parsed.nextCursor===next.cursor)||(parsed.nextLotCursor&&parsed.nextLotCursor===next.lotCursor))throw Error('cursor');
      setData(parsed);setPages(next);setLot(parsed.lots.some(x=>x.lotId===lot)?lot:'');
    }catch{if(!c.signal.aborted){setData(null);setMessage('Не удалось загрузить складские акты. Начните с первой страницы.');}}
    finally{if(op.current===c){op.current=null;setBusy(false);}}
  }
  async function send(snap:Pending){
    if(op.current)return;
    const c=new AbortController();op.current=c;setBusy(true);setPending(snap);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal:c.signal});
      if(c.signal.aborted)return;
      if([401,403,404].includes(csrf.status)){deny();return;}
      if(!csrf.ok)throw Error('csrf');
      const token=await csrf.json();
      if(c.signal.aborted)return;
      if(typeof token.csrfToken!=='string')throw Error('csrf');
      const res=await fetch(snap.path,{method:'POST',credentials:'include',cache:'no-store',signal:c.signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken,'Idempotency-Key':snap.key},body:snap.body});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){deny();return;}
      if(res.status===409||res.status===422){
        setPending(null);setData(null);setDecision(null);
        setMessage('Операция не подтверждена. Проверьте остаток, состояние акта и обновите журнал.');return;
      }
      if(!res.ok)throw Error('unknown');
      const raw=await res.json();
      if(c.signal.aborted)return;
      if(raw.syntheticOnly!==true||!id(raw[snap.field])||
        !((res.status===201&&raw.replayed===false)||(res.status===200&&raw.replayed===true)))throw Error('receipt');
      setPending(null);setData(null);setDecision(null);setDecisionNote('');setLot('');setNote('');
      setMessage((snap.field==='documentId'?'Акт создан. Остатки не изменены. ':'Решение записано. ')+raw[snap.field]+'. Обновите журнал.');
    }catch{if(!c.signal.aborted)setMessage('Результат неизвестен. Повторите исходную операцию с тем же ключом.');}
    finally{if(op.current===c){op.current=null;setBusy(false);}}
  }
  const locked=busy||pending!==null;
  return <section className="ui-page" aria-label="Акты списания запчастей" style={{overflowWrap:'anywhere'}}>
    <h1>Списание запчастей (dev)</h1>
    <p>Кладовщик готовит акт. Другой управляющий утверждает или отклоняет его. Только утверждение уменьшает физический остаток и размещение; резерв защищён.</p>
    <label>UUID организации <input value={org} disabled={locked} onChange={e=>{clear();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input value={loc} disabled={locked} onChange={e=>{clear();setLoc(e.target.value);}}/></label>
    <label>Поиск партии или артикула <input value={query} maxLength={80} disabled={locked} onChange={e=>{setQuery(e.target.value);setData(null);setPages({});setLot('');setDecision(null);}}/></label>
    <label>Статус актов <select value={status} disabled={locked} onChange={e=>{setStatus(e.target.value);setData(null);setPages({});setDecision(null);}}>
      <option value="ALL">Все</option>{Object.entries(labels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <button type="button" disabled={!validScope||locked} onClick={()=>void load()}>Загрузить партии и акты</button>
    {denied&&<p role="alert">Доступ к списанию запрещён.</p>}
    {busy&&<p role="status">Выполняется запрос…</p>}
    {message&&<p role="status">{message}</p>}
    {data?.canDraft&&<section aria-label="Подготовка списания">
      <h2>Новый акт</h2>
      <label>Партия для списания <select value={lot} disabled={locked} onChange={e=>setLot(e.target.value)}>
        <option value="">Выберите партию</option>{data.lots.map(x=><option key={x.lotId} value={x.lotId}>{x.article} · {x.batchCode} · {x.binCode} · доступно {x.available}</option>)}</select></label>
      {data.nextLotCursor&&<button type="button" disabled={locked} onClick={()=>void load({...pages,lotCursor:data.nextLotCursor!})}>Следующие партии</button>}
      {pages.lotCursor&&<button type="button" disabled={locked} onClick={()=>void load({...pages,lotCursor:undefined})}>Первые партии</button>}
      <label>Количество <input type="number" min={1} max={1000000000} step={1} value={qty} disabled={locked} onChange={e=>setQty(e.target.value)}/></label>
      <label>Причина списания <select value={reason} disabled={locked} onChange={e=>setReason(e.target.value as Reason)}>
        {Object.entries(reasons).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label>Обоснование списания <input value={note} maxLength={240} disabled={locked} onChange={e=>setNote(e.target.value)}/></label>
      <button type="button" disabled={!canDraft||locked} onClick={()=>void send({path:base,key:crypto.randomUUID(),field:'documentId',
        body:JSON.stringify({locationId:loc.trim(),lotId:lot,quantity:Number(qty),reasonCode:reason,note})})}>Создать акт списания</button>
    </section>}
    {data&&<section aria-label="Журнал списания">
      <h2>Журнал актов списания</h2>
      {!data.items.length?<p>Актов нет.</p>:<ol>{data.items.map(x=><li key={x.documentId}>
        <strong>{x.article} · {x.quantity} ед. · {labels[x.status]}</strong>
        <p>Партия {x.batchCode}, ячейка {x.binCode}. {reasons[x.reasonCode]}: {x.note}</p>
        <p>Акт {x.documentId} · автор {x.createdBy} · {new Date(x.createdAt).toLocaleString('ru-RU')}</p>
        {x.decision&&<details><summary>Решение и проводки</summary><p>{x.decision.note} · {x.decision.actorId}</p>
          {x.decision.stockEventId&&<p>Списание {x.decision.stockEventId} · снятие размещения {x.decision.lotDebitId}</p>}</details>}
        {x.canReview&&<><button type="button" disabled={locked} onClick={()=>{setDecision({id:x.documentId,action:'APPROVE'});setDecisionNote('');}}>Утвердить акт</button>
          <button type="button" disabled={locked} onClick={()=>{setDecision({id:x.documentId,action:'REJECT'});setDecisionNote('');}}>Отклонить акт</button></>}
        {x.canCancel&&<button type="button" disabled={locked} onClick={()=>{setDecision({id:x.documentId,action:'CANCEL'});setDecisionNote('');}}>Отменить акт</button>}
        {decision?.id===x.documentId&&<div role="group" aria-label="Подтверждение решения">
          <p>{decision.action==='APPROVE'?'Подтвердите физическое списание.':decision.action==='REJECT'?'Отклонение не меняет остатки.':'Отмена не меняет остатки.'}</p>
          <label>Обоснование решения <input maxLength={240} value={decisionNote} disabled={locked} onChange={e=>setDecisionNote(e.target.value)}/></label>
          <button type="button" disabled={!canDecide||locked} onClick={()=>void send({path:base+'/'+decision.id+'/decisions',key:crypto.randomUUID(),field:'decisionId',
            body:JSON.stringify({locationId:loc.trim(),action:decision.action,note:decisionNote})})}>Подтвердить решение</button>
          <button type="button" disabled={locked} onClick={()=>setDecision(null)}>Назад</button>
        </div>}
      </li>)}</ol>}
      {data.nextCursor&&<button type="button" disabled={locked} onClick={()=>void load({...pages,cursor:data.nextCursor!})}>Следующие акты</button>}
      {pages.cursor&&<button type="button" disabled={locked} onClick={()=>void load({...pages,cursor:undefined})}>Первые акты</button>}
    </section>}
    {pending&&<button type="button" disabled={busy} onClick={()=>void send(pending)}>Повторить исходную операцию</button>}
  </section>;
}
