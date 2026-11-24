import React from 'react';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ident=(v:unknown):v is string=>typeof v==='string'&&uuid.test(v);
const integer=(v:unknown):v is string=>typeof v==='string'&&/^-?(0|[1-9][0-9]{0,18})$/.test(v);
const cursor=(v:unknown):v is string|null=>v===null||typeof v==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(v);
const counts=['total','discrepancies','unallocated','reserved','outOfStock','filtered'] as const;
const fields=['onHand','reserved','allocated','unallocated','available'] as const;
const statuses={ALL:'Все артикулы',DISCREPANCY:'Расхождения',UNALLOCATED:'Неразмещённые',RESERVED:'Есть резерв',OUT_OF_STOCK:'Нет свободного остатка'} as const;
const actions:Record<string,string>={RECEIPT:'Приход',RESERVE:'Резерв',RELEASE:'Освобождение резерва',ISSUE:'Выдача',RETURN:'Возврат',COUNT_GAIN:'Излишек',COUNT_LOSS:'Недостача',TRANSFER_IN:'Перемещение: приход',TRANSFER_OUT:'Перемещение: расход',WRITE_OFF:'Списание',SUPPLIER_RETURN:'Возврат поставщику'};
type Status=keyof typeof statuses;
type Kind='lots'|'events';
type Row={skuId:string;article:string;onHand:string;reserved:string;allocated:string;unallocated:string;available:string;discrepancy:boolean};
type Report={syntheticOnly:true;organizationId:string;locationId:string;summary:Record<typeof counts[number],number>;items:Row[];nextCursor:string|null};
type Lot={lotId:string;createdAt:string;batchCode:string;binCode:string;allocated:string};
type Event={eventId:string;createdAt:string;action:string;quantity:string};
type Detail={syntheticOnly:true;organizationId:string;locationId:string;skuId:string;kind:Kind;items:(Lot|Event)[];nextCursor:string|null};
const stamp=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v));
function scoped(raw:unknown,org:string,loc:string){
  if(!raw||typeof raw!=='object')throw Error('shape');
  const d=raw as Report;
  if(d.syntheticOnly!==true||d.organizationId!==org.toLowerCase()||d.locationId!==loc.toLowerCase()||
    !cursor(d.nextCursor)||!Array.isArray(d.items)||d.items.length>50||(d.nextCursor!==null&&d.items.length!==50))throw Error('scope');
}
function parseReport(raw:unknown,org:string,loc:string):Report{
  scoped(raw,org,loc);
  const d=raw as Report;
  if(!d.summary||!counts.every(k=>Number.isSafeInteger(d.summary[k])&&d.summary[k]>=0&&d.summary[k]<=d.summary.total)||
    d.items.length>d.summary.filtered||!d.items.every(x=>{
      if(!x||!ident(x.skuId)||typeof x.article!=='string'||!fields.every(k=>integer(x[k]))||typeof x.discrepancy!=='boolean')return false;
      const [on,res,alloc,unalloc,avail]=fields.map(k=>BigInt(x[k]));
      return on-alloc===unalloc&&on-res===avail&&x.discrepancy===(on<0n||res<0n||res>on||alloc<0n||alloc>on);
    })||new Set(d.items.map(x=>x.skuId)).size!==d.items.length)throw Error('report');
  return d;
}
function parseDetail(raw:unknown,org:string,loc:string,sku:string,kind:Kind):Detail{
  scoped(raw,org,loc);
  const d=raw as Detail;
  if(d.skuId!==sku||d.kind!==kind||!d.items.every(x=>x&&stamp(x.createdAt)&&
    (kind==='lots'?'lotId' in x&&ident(x.lotId)&&typeof x.batchCode==='string'&&typeof x.binCode==='string'&&integer(x.allocated):
      'eventId' in x&&ident(x.eventId)&&typeof x.action==='string'&&Object.hasOwn(actions,x.action)&&integer(x.quantity)&&BigInt(x.quantity)>0n))||
    new Set(d.items.map(x=>'lotId' in x?x.lotId:x.eventId)).size!==d.items.length)throw Error('detail');
  return d;
}
export function WarehouseReconciliation(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [query,setQuery]=React.useState('');
  const [status,setStatus]=React.useState<Status>('ALL');
  const [report,setReport]=React.useState<Report|null>(null);
  const [page,setPage]=React.useState<string|null>(null);
  const [selected,setSelected]=React.useState<Row|null>(null);
  const [detail,setDetail]=React.useState<Detail|null>(null);
  const [detailPage,setDetailPage]=React.useState<string|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState('');
  const op=React.useRef<AbortController|null>(null);
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/warehouse/reconciliation';
  const valid=ident(org.trim())&&ident(loc.trim());
  function reset(){
    op.current?.abort();op.current=null;setReport(null);setPage(null);setSelected(null);setDetail(null);setDetailPage(null);setBusy(false);setError('');
  }
  React.useEffect(()=>()=>op.current?.abort(),[]);
  async function read(url:string,accept:(raw:unknown)=>void){
    if(op.current)return;
    const c=new AbortController();op.current=c;setBusy(true);setError('');
    try{
      const res=await fetch(url,{credentials:'include',cache:'no-store',signal:c.signal});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){reset();setError('Доступ к сверке склада запрещён.');return;}
      if(!res.ok)throw Error('http');
      const raw:unknown=await res.json();if(!c.signal.aborted)accept(raw);
    }catch{if(!c.signal.aborted){setReport(null);setSelected(null);setDetail(null);setError('Не удалось прочитать сверку. Обновите с первой страницы.');}}
    finally{if(op.current===c){op.current=null;setBusy(false);}}
  }
  async function load(next:string|null=null){
    if(!valid||op.current)return;
    setSelected(null);setDetail(null);setDetailPage(null);setReport(null);
    const params=new URLSearchParams({locationId:loc.trim(),q:query.trim(),status});
    if(next)params.set('cursor',next);
    await read(base+'?'+params,raw=>{
      const d=parseReport(raw,org.trim(),loc.trim());
      if(d.nextCursor&&d.nextCursor===next)throw Error('cursor');
      setReport(d);setPage(next);
    });
  }
  async function open(row:Row,kind:Kind,next:string|null=null){
    if(op.current)return;
    setSelected(row);setDetail(null);
    const params=new URLSearchParams({locationId:loc.trim(),kind});
    if(next)params.set('cursor',next);
    await read(base+'/'+row.skuId+'?'+params,raw=>{
      const d=parseDetail(raw,org.trim(),loc.trim(),row.skuId,kind);
      if(d.nextCursor&&d.nextCursor===next)throw Error('cursor');
      setDetail(d);setDetailPage(next);
    });
  }
  const number=(v:string|number)=>BigInt(v).toLocaleString('ru-RU');
  return <section className="ui-page" aria-label="Сверка складских остатков" style={{overflowWrap:'anywhere'}}>
    <h1>Сверка складских остатков (dev)</h1>
    <p>Физический остаток, резерв и размещение — разные величины. Сверка читает журнал, не проводит корректировки. Для физической проверки используйте инвентаризацию.</p>
    <label>UUID организации <input value={org} onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input value={loc} onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>Поиск артикула <input maxLength={80} value={query} onChange={e=>{reset();setQuery(e.target.value);}}/></label>
    <label>Фильтр сверки <select value={status} onChange={e=>{reset();setStatus(e.target.value as Status);}}>
      {Object.entries(statuses).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <button type="button" disabled={!valid||busy} onClick={()=>void load()}>Обновить сверку</button>
    {busy&&<p role="status">Загрузка сверки…</p>}
    {error&&<p role="alert">{error}</p>}
    {report&&<>
      <section aria-label="Сводка сверки">
        <h2>Сводка по поиску артикула</h2>
        <p>Артикулов: {number(report.summary.total)} · расхождения: {number(report.summary.discrepancies)} · неразмещённые: {number(report.summary.unallocated)} · с резервом: {number(report.summary.reserved)} · без свободного остатка: {number(report.summary.outOfStock)}.</p>
        <p>По выбранному фильтру: {number(report.summary.filtered)}. Сводка и текущая страница рассчитаны на момент одного запроса.</p>
        {report.summary.discrepancies>0&&<p role="alert">Обнаружены расхождения ledger. Не исправляйте их прямыми записями; передайте ответственному сотруднику.</p>}
      </section>
      <section aria-label="Артикулы сверки">
        <h2>Остатки по артикулам</h2>
        {report.items.length===0?<p>Нет артикулов по выбранному фильтру.</p>:<ul>{report.items.map(x=><li key={x.skuId}>
          <strong>{x.article}</strong> · {x.discrepancy?'Расхождение':'Согласовано'}
          <p>Физический: {number(x.onHand)} · резерв: {number(x.reserved)} · размещено: {number(x.allocated)} · не размещено: {number(x.unallocated)} · доступно: {number(x.available)}</p>
          <button type="button" disabled={busy} onClick={()=>void open(x,'lots')}>Партии {x.article}</button>{' '}
          <button type="button" disabled={busy} onClick={()=>void open(x,'events')}>Движения {x.article}</button>
        </li>)}</ul>}
        {report.nextCursor&&<button type="button" disabled={busy} onClick={()=>void load(report.nextCursor)}>Следующие артикулы</button>}
        {page&&<button type="button" disabled={busy} onClick={()=>void load()}>Первые артикулы</button>}
      </section>
    </>}
    {selected&&detail&&<section aria-label="Детализация артикула">
      <h2>{selected.article}: {detail.kind==='lots'?'Партии':'Физические движения и резервы'}</h2>
      <p>Детализация прочитана отдельным запросом, остатки могут измениться после сводки.</p>
      {detail.items.length===0?<p>Записей нет.</p>:<ul>{detail.items.map(x=>'lotId' in x?
        <li key={x.lotId}>{x.batchCode} · {x.binCode} · размещено {number(x.allocated)}<p>Партия {x.lotId}</p></li>:
        <li key={x.eventId}>{actions[x.action]} · {number(x.quantity)} · {new Date(x.createdAt).toLocaleString('ru-RU')}<p>Событие {x.eventId}</p></li>)}</ul>}
      {detail.nextCursor&&<button type="button" disabled={busy} onClick={()=>void open(selected,detail.kind,detail.nextCursor)}>Следующая страница детализации</button>}
      {detailPage&&<button type="button" disabled={busy} onClick={()=>void open(selected,detail.kind)}>Первая страница детализации</button>}
      <button type="button" disabled={busy} onClick={()=>{setSelected(null);setDetail(null);setDetailPage(null);}}>Закрыть детализацию</button>
    </section>}
    <p><a href="/staff/warehouse-stocktake">Перейти к инвентаризации</a></p>
  </section>;
}
