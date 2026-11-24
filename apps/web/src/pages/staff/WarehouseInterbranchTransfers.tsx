import React from 'react';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const integer=(x:unknown):x is string=>typeof x==='string'&&/^(0|[1-9][0-9]{0,18})$/.test(x);
type Lot={lotId:string;article:string;batchCode:string;binCode:string;allocated:string;available:string};
type Mode='INTERBRANCH'|'LOCAL';
type Cursors={sourceCursor?:string;destinationCursor?:string;cursor?:string};
const cursorValid=(x:unknown):x is string|null=>x===null||typeof x==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(x);
type Entry={transferId:string;createdAt:string;sourceLotId:string;destinationLotId:string;
  article:string;quantity:string;actorId:string|null;debitStockId?:string;creditStockId?:string;debitAllocationId?:string;creditAllocationId?:string};
type Options={items:Lot[];truncated:boolean;nextCursor:string|null};
type Snapshot={syntheticOnly:true;mode:Mode;source:Options;destination:Options;items:Entry[];nextCursor:string|null};
type Pending={body:string;key:string;path:string};
function parse(raw:unknown,mode:Mode):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const d=raw as Snapshot;
  if(d.syntheticOnly!==true||d.mode!==mode||!cursorValid(d.nextCursor)||(d.nextCursor!==null&&d.items?.length!==50)||
    ![d.source,d.destination].every(o=>o&&typeof o.truncated==='boolean'&&Array.isArray(o.items)&&
      o.items.length<=50&&cursorValid(o.nextCursor)&&o.truncated===(o.nextCursor!==null)&&(!o.truncated||o.items.length===50)&&o.items.every(l=>l&&uuid.test(l.lotId)&&typeof l.article==='string'&&
        typeof l.batchCode==='string'&&typeof l.binCode==='string'&&integer(l.allocated)&&
        integer(l.available)&&BigInt(l.available)<=BigInt(l.allocated))&&
      new Set(o.items.map(l=>l.lotId)).size===o.items.length)||
    !Array.isArray(d.items)||d.items.length>50||!d.items.every(x=>x&&
      [x.transferId,x.sourceLotId,x.destinationLotId,...(mode==='LOCAL'?[x.debitAllocationId,x.creditAllocationId]:[x.debitStockId,x.creditStockId])].every(i=>typeof i==='string'&&uuid.test(i))&&
      (x.actorId===null||uuid.test(x.actorId))&&typeof x.article==='string'&&integer(x.quantity)&&
      BigInt(x.quantity)>0n&&typeof x.createdAt==='string'&&Number.isFinite(Date.parse(x.createdAt)))||
    new Set(d.items.map(x=>x.transferId)).size!==d.items.length)throw Error('shape');
  return d;
}
export function WarehouseInterbranchTransfers(){
  const [mode,setMode]=React.useState<Mode>('INTERBRANCH');
  const [pages,setPages]=React.useState<Cursors>({});
  const [org,setOrg]=React.useState('');
  const [src,setSrc]=React.useState('');
  const [dst,setDst]=React.useState('');
  const [query,setQuery]=React.useState('');
  const [source,setSource]=React.useState('');
  const [destination,setDestination]=React.useState('');
  const [quantity,setQuantity]=React.useState('1');
  const [data,setData]=React.useState<Snapshot|null>(null);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [confirm,setConfirm]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [state,setState]=React.useState<'idle'|'ready'|'denied'|'error'>('idle');
  const op=React.useRef<AbortController|null>(null);
  const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/warehouse/interbranch-transfers';
  const targetLoc=mode==='LOCAL'?src:dst;
  const valid=[org,src,targetLoc].every(v=>uuid.test(v.trim()))&&(mode==='LOCAL'||src.trim().toLowerCase()!==dst.trim().toLowerCase());
  const selected=data?.source.items.find(l=>l.lotId===source);
  const targets=data?.destination.items.filter(l=>l.article===selected?.article&&l.lotId!==source)??[];
  const allowed=!!selected&&targets.some(l=>l.lotId===destination)&&integer(quantity)&&
    BigInt(quantity)>0n&&BigInt(quantity)<=1000000000n&&BigInt(quantity)<=BigInt(selected.available);
  function reset(){
    op.current?.abort();op.current=null;setData(null);setSource('');setDestination('');
    setQuantity('1');setPages({});setConfirm(false);setPending(null);setBusy(false);setMessage('');setState('idle');
  }
  React.useEffect(()=>()=>op.current?.abort(),[]);
  function deny(){reset();setState('denied');}
  async function load(next:Cursors={},keepSelection=false){
    if(!valid||op.current||pending)return;
    const controller=new AbortController();op.current=controller;setBusy(true);setConfirm(false);
    if(!keepSelection){setData(null);setSource('');setDestination('');}
    try{
      const params=new URLSearchParams({sourceLocationId:src.trim(),destinationLocationId:targetLoc.trim(),q:query.trim(),mode});
      for(const [key,value] of Object.entries(next))if(value)params.set(key,value);
      const res=await fetch(path+'?'+params,{credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){deny();return;}
      if(!res.ok)throw Error('read');
      const parsed=parse(await res.json(),mode);
      if(controller.signal.aborted)return;
      if((parsed.nextCursor&&parsed.nextCursor===next.cursor)||
        (parsed.source.nextCursor&&parsed.source.nextCursor===next.sourceCursor)||
        (parsed.destination.nextCursor&&parsed.destination.nextCursor===next.destinationCursor))throw Error('cursor');
      const kept=keepSelection?parsed.source.items.find(x=>x.lotId===source):undefined;
      setData(parsed);setPages(next);setSource(kept?.lotId??'');
      setDestination(kept&&parsed.destination.items.some(x=>x.lotId===destination&&x.article===kept.article&&x.lotId!==kept.lotId)?destination:'');setState('ready');
    }catch{if(!controller.signal.aborted){setData(null);setState('error');setMessage('Не удалось прочитать партии и журнал. Начните с первой страницы.');}}
    finally{if(op.current===controller){op.current=null;if(!controller.signal.aborted)setBusy(false);}}
  }
  async function submit(snap:Pending){
    if(op.current||state!=='ready'||snap.path!==path)return;
    const controller=new AbortController();op.current=controller;setBusy(true);setPending(snap);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(csrf.status)){deny();return;}
      if(!csrf.ok)throw Error('csrf');
      const token=await csrf.json();
      if(controller.signal.aborted)return;
      if(typeof token.csrfToken!=='string')throw Error('csrf');
      const res=await fetch(snap.path,{method:'POST',credentials:'include',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken,'Idempotency-Key':snap.key},body:snap.body});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){deny();return;}
      if(res.status===409||res.status===422){
        setPending(null);setConfirm(false);setData(null);setState('error');
        setMessage('Перемещение не подтверждено. Обновите партии и остатки.');return;
      }
      if(!res.ok)throw Error('unknown');
      const raw=await res.json();
      if(controller.signal.aborted)return;
      if(raw.syntheticOnly!==true||typeof raw.transferId!=='string'||!uuid.test(raw.transferId)||
        !((res.status===201&&raw.replayed===false)||(res.status===200&&raw.replayed===true)))throw Error('receipt');
      setPending(null);setConfirm(false);setData(null);setState('idle');setSource('');setDestination('');
      setMessage('Перемещение подтверждено: '+raw.transferId+'. Обновите партии и журнал.');
    }catch{if(!controller.signal.aborted)setMessage('Результат неизвестен. Повторите исходную операцию с тем же ключом.');}
    finally{if(op.current===controller){op.current=null;if(!controller.signal.aborted)setBusy(false);}}
  }
  return <section className="ui-page" aria-label="Межфилиальные перемещения" style={{overflowWrap:'anywhere'}}>
    <h1>Перемещение запчастей (dev)</h1>
    <label>Вид перемещения <select value={mode} disabled={!!pending||busy} onChange={e=>{reset();setMode(e.target.value as Mode);}}>
      <option value="INTERBRANCH">Между филиалами</option><option value="LOCAL">Между ячейками филиала</option></select></label>
    <p>{mode==='LOCAL'?'Нужно право WAREHOUSE выбранного филиала. Перераспределяется размещение одного SKU, общий физический остаток и резерв не меняются.':'Требуется действующее право WAREHOUSE в обоих филиалах. Подтверждайте только фактически переданные партии. Списываются и приходуются одинаковые артикулы одной организации; резерв не перемещается.'}</p>
    <label>UUID организации <input value={org} disabled={!!pending||busy} onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала отправления <input value={src} disabled={!!pending||busy} onChange={e=>{reset();setSrc(e.target.value);}}/></label>
    {mode==='INTERBRANCH'&&<label>UUID филиала назначения <input value={dst} disabled={!!pending||busy} onChange={e=>{reset();setDst(e.target.value);}}/></label>}
    <label>Поиск артикула <input value={query} maxLength={80} disabled={!!pending||busy} onChange={e=>{setQuery(e.target.value);setConfirm(false);setData(null);setPages({});setSource('');setDestination('');}}/></label>
    <button type="button" disabled={!valid||busy||!!pending} onClick={()=>void load()}>Загрузить партии и журнал</button>
    {state==='denied'&&<p role="alert">Доступ запрещён: нужны права склада в обоих филиалах.</p>}
    {busy&&<p role="status">Запрос выполняется…</p>}
    {message&&<p role="status">{message}</p>}
    {data&&<>
      {(data.source.truncated||data.destination.truncated)&&<p>Показано до 50 партий на страницу. Используйте продолжение списка каждой стороны.</p>}
      <label>Исходная партия <select value={source} disabled={busy||!!pending}
        onChange={e=>{setSource(e.target.value);setDestination('');setConfirm(false);}}>
        <option value="">Выберите партию</option>
        {data.source.items.map(l=><option key={l.lotId} value={l.lotId}>{l.article} · {l.batchCode} · {l.binCode} · доступно {l.available}</option>)}
      </select></label>
      {data.source.nextCursor&&<button type="button" disabled={busy||!!pending} onClick={()=>void load({...pages,sourceCursor:data.source.nextCursor!},true)}>Следующие исходные партии</button>}
      {pages.sourceCursor&&<button type="button" disabled={busy||!!pending} onClick={()=>void load({...pages,sourceCursor:undefined},true)}>Первые исходные партии</button>}
      <label>Целевая партия того же артикула <select value={destination} disabled={busy||!!pending}
        onChange={e=>{setDestination(e.target.value);setConfirm(false);}}>
        <option value="">Выберите партию</option>
        {targets.map(l=><option key={l.lotId} value={l.lotId}>{l.article} · {l.batchCode} · {l.binCode}</option>)}
      </select></label>
      {data.destination.nextCursor&&<button type="button" disabled={busy||!!pending} onClick={()=>void load({...pages,destinationCursor:data.destination.nextCursor!},true)}>Следующие целевые партии</button>}
      {pages.destinationCursor&&<button type="button" disabled={busy||!!pending} onClick={()=>void load({...pages,destinationCursor:undefined},true)}>Первые целевые партии</button>}
      {selected&&!targets.length&&<p>Подходящая целевая партия не найдена. Перейдите на следующую страницу, уточните поиск или подготовьте целевую партию.</p>}
      <label>Количество <input type="number" min={1} max={1000000000} step={1} value={quantity}
        disabled={busy||!!pending} onChange={e=>{setQuantity(e.target.value);setConfirm(false);}}/></label>
      {!confirm?<button type="button" disabled={!allowed||busy||!!pending} onClick={()=>setConfirm(true)}>Подготовить перемещение</button>:
        <><p>Подтвердите фактическую передачу {quantity} ед. артикула {selected?.article}.</p>
          <button type="button" disabled={!allowed||busy||!!pending} onClick={()=>void submit({path,key:crypto.randomUUID(),
            body:JSON.stringify({...(mode==='LOCAL'?{mode}:{}),sourceLocationId:src.trim(),destinationLocationId:targetLoc.trim(),sourceLotId:source,
              destinationLotId:destination,quantity:Number(quantity)})})}>Подтвердить перемещение</button>
          <button type="button" disabled={busy||!!pending} onClick={()=>setConfirm(false)}>Отмена</button></>}
      <h2>Журнал перемещений выбранного направления</h2>
      {data.items.length===0?<p>Перемещений пока нет.</p>:<ol>{data.items.map(x=><li key={x.transferId}>
        <strong>{x.article} · {x.quantity} ед.</strong> · {new Date(x.createdAt).toLocaleString('ru-RU')}
        <p>Перемещение {x.transferId} · автор {x.actorId??'не зафиксирован в старом журнале'}</p>
        <details><summary>Связанные проводки</summary><p>Из партии {x.sourceLotId} в {x.destinationLotId}</p>
          <p>{mode==='LOCAL'?<>Снятие размещения {x.debitAllocationId} · размещение {x.creditAllocationId}</>:<>Расход {x.debitStockId} · приход {x.creditStockId}</>}</p></details>
      </li>)}</ol>}
      {pages.cursor&&<button type="button" disabled={busy||!!pending} onClick={()=>void load({...pages,cursor:undefined},true)}>Первая страница журнала</button>}
      {data.nextCursor&&<button type="button" disabled={busy||!!pending} onClick={()=>void load({...pages,cursor:data.nextCursor!},true)}>Следующая страница журнала</button>}
    </>}
    {pending&&<button type="button" disabled={busy} onClick={()=>void submit(pending)}>Повторить исходную операцию</button>}
  </section>;
}
