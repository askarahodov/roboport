import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sources={ALL:'Все источники',WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение'};
const stages={ALL:'Все этапы',NEW:'Новое',QUALIFIED:'Обработано',BOOKED:'Записан',LOST:'Отказ'};
type Source=keyof typeof sources;
type Stage=keyof typeof stages;
type Row={leadId:string;customerLinkId:string;ownerId:string;sourceCode:Exclude<Source,'ALL'>;
  status:Exclude<Stage,'ALL'>;createdAt:string;visitNumber:number};
type Page={items:Row[];nextCursor:string|null;timeZone?:string};
function parse(raw:unknown,org:string,loc:string,link:string,source:Source,stage:Stage,owner:string,from:string,to:string):Page{
  if(!raw||typeof raw!=='object')throw Error('page');
  const p=raw as Page&{syntheticOnly:boolean;organizationId:string;locationId:string;
    customerLinkId:string|null;sourceCode:Source;stageCode:Stage;ownerId?:string|null;fromDay?:string|null;toDay?:string|null};
  if(p.syntheticOnly!==true||p.organizationId!==org.toLowerCase()||p.locationId!==loc.toLowerCase()||
    p.customerLinkId!==(link?link.toLowerCase():null)||p.sourceCode!==source||p.stageCode!==stage||
    (p.ownerId??null)!==(owner?owner.toLowerCase():null)||(p.fromDay??null)!==(from||null)||(p.toDay??null)!==(to||null)||
    (from&&typeof p.timeZone!=='string')||
    !Array.isArray(p.items)||p.items.length>25||
    !(p.nextCursor===null||(typeof p.nextCursor==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(p.nextCursor)&&p.items.length===25)))
    throw Error('page');
  if(!p.items.every(r=>r&&[r.leadId,r.customerLinkId,r.ownerId].every(v=>typeof v==='string'&&uuid.test(v))&&
    ['WEB','PHONE','WALK_IN'].includes(r.sourceCode)&&['NEW','QUALIFIED','BOOKED','LOST'].includes(r.status)&&
    (!link||r.customerLinkId===link.toLowerCase())&&(source==='ALL'||r.sourceCode===source)&&
    (stage==='ALL'||r.status===stage)&&(!owner||r.ownerId===owner.toLowerCase())&&Number.isSafeInteger(r.visitNumber)&&r.visitNumber>=1&&
    typeof r.createdAt==='string'&&Number.isFinite(Date.parse(r.createdAt)))||
    new Set(p.items.map(r=>r.leadId)).size!==p.items.length)throw Error('rows');
  if(p.timeZone){
    const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:p.timeZone,year:'numeric',month:'2-digit',day:'2-digit'});
    if(from&&p.items.some(r=>{const parts=fmt.formatToParts(new Date(r.createdAt));const day=['year','month','day'].map(k=>parts.find(x=>x.type===k)?.value).join('-');return day<from||day>to;}))throw Error('period');
  }
  return p;
}
export function CrmLeadSearch({org,loc,disabled,onOpen,onDenied}:{org:string;loc:string;disabled:boolean;
  onOpen:(id:string)=>void;onDenied:()=>void}){
  const [link,setLink]=React.useState('');
  const [source,setSource]=React.useState<Source>('ALL');
  const [stage,setStage]=React.useState<Stage>('ALL');
  const [owner,setOwner]=React.useState(''),[from,setFrom]=React.useState(''),[to,setTo]=React.useState('');
  const [exportStatus,setExportStatus]=React.useState('');
  const [data,setData]=React.useState<Page|null>(null);
  const [position,setPosition]=React.useState<string|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'error'>('idle');
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  function reset(){active.current?.abort();setData(null);setPosition(null);setStatus('idle');setExportStatus('');}
  const validFilters=(!link||uuid.test(link))&&(!owner||uuid.test(owner))&&((!from&&!to)||!!from&&!!to&&from<=to&&Date.parse(to)-Date.parse(from)<=30*86400000);
  function filters(){const p=new URLSearchParams({sourceCode:source,stageCode:stage});if(link)p.set('customerLinkId',link);if(owner)p.set('ownerId',owner);if(from&&to){p.set('fromDay',from);p.set('toDay',to);}return p;}
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/locations/'+encodeURIComponent(loc)+'/crm/lead-search';
  async function search(cursor:string|null=null){
    if(disabled||!uuid.test(org)||!uuid.test(loc)||!validFilters)return;
    active.current?.abort();const control=new AbortController();active.current=control;
    setData(null);setPosition(null);setStatus('loading');
    setExportStatus('');const params=filters();
    if(cursor)params.set('cursor',cursor);
    try{
      const r=await fetch(root+'?'+params,
        {credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(r.status)){reset();onDenied();return;}
      if(!r.ok)throw Error('http');
      const parsed=parse(await r.json(),org,loc,link,source,stage,owner,from,to);
      if(control.signal.aborted)return;
      if(cursor&&cursor===parsed.nextCursor)throw Error('cursor');
      setData(parsed);setPosition(cursor);setStatus('ready');
    }catch{if(!control.signal.aborted)setStatus('error');}
  }
  async function exportAll(){
    if(disabled||!validFilters||status!=='ready'||exportStatus==='Выгрузка…')return;
    active.current?.abort();const control=new AbortController();active.current=control;
    setExportStatus('Выгрузка…');
    try{
      const r=await fetch(root+'/export?'+filters(),{credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(r.status)){reset();onDenied();return;}
      if(!r.ok)throw Error('export');
      const expected=r.headers.get('X-CRM-SHA256'),count=r.headers.get('X-CRM-Row-Count');
      if(!expected||!(/^[0-9a-f]{64}$/).test(expected)||count===null||!(/^(0|[1-9][0-9]{0,2})$/).test(count)||
        Number(count)>250||!r.headers.get('content-type')?.startsWith('text/csv'))throw Error('receipt');
      const bytes=await r.arrayBuffer();
      if(bytes.byteLength>262144)throw Error('size');
      const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
      if(actual!==expected)throw Error('digest');
      if(control.signal.aborted)return;
      const url=URL.createObjectURL(new Blob([bytes],{type:'text/csv'}));
      const a=document.createElement('a');a.href=url;a.download='synthetic-crm-leads.csv';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
      setExportStatus('Выгружено обращений: '+count+'. Только искусственные данные.');
    }catch{if(!control.signal.aborted)setExportStatus('Выгрузка не подтверждена. Уточните фильтры (до 250 обращений) и повторите.');}
  }
  return <section aria-label="Поиск обращений CRM">
    <h2>Поиск и история клиента</h2>
    <p>Поиск по всем доступным обращениям филиала, а не только текущей странице.
      Используйте UUID подтверждённой связи, без номера телефона или VIN.</p>
    <label>UUID связи для поиска <input aria-label="UUID связи для поиска" value={link}
      disabled={disabled} onChange={e=>{reset();setLink(e.target.value.trim());}}/></label>
    <label>Источник поиска <select aria-label="Источник поиска" value={source} disabled={disabled}
      onChange={e=>{reset();setSource(e.target.value as Source);}}>
      {Object.entries(sources).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <label>Этап поиска <select aria-label="Этап поиска" value={stage} disabled={disabled}
      onChange={e=>{reset();setStage(e.target.value as Stage);}}>
      {Object.entries(stages).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <label>UUID ответственного <input value={owner} disabled={disabled} onChange={e=>{reset();setOwner(e.target.value.trim());}}/></label>
    <label>Создано с <input type="date" value={from} disabled={disabled} onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>Создано по <input type="date" value={to} disabled={disabled} onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <p>Местные сутки филиала, до 31 дня. Выгрузка до 250 обращений.</p>
    <button type="button" disabled={disabled||status==='loading'||!validFilters}
      onClick={()=>void search()}>Найти обращения</button>
    {status==='loading'&&<p role="status">Поиск обращений…</p>}
    {status==='error'&&<p role="alert">Поиск недоступен или этап изменился. Повторите поиск с первой страницы.</p>}
    {status==='ready'&&data&&<>
      {data.timeZone&&<p>Часовой пояс филиала: {data.timeZone}</p>}
      <button type="button" disabled={disabled||exportStatus==='Выгрузка…'} onClick={()=>void exportAll()}>Скачать все найденные обращения (CSV)</button>
      {exportStatus&&<p role="status">{exportStatus}</p>}
      {data.items.length===0?<p>Подходящих обращений нет.</p>:<div style={{overflowX:'auto'}}><table>
        <caption>Найденные обращения</caption><thead><tr><th>Дата</th><th>Цикл</th><th>Источник</th>
          <th>Этап</th><th>Ответственный</th><th>Действие</th></tr></thead>
        <tbody>{data.items.map(r=><tr key={r.leadId}><td>{new Date(r.createdAt).toLocaleString('ru-RU',data.timeZone?{timeZone:data.timeZone}:undefined)}</td>
          <td>№{r.visitNumber}</td><td>{sources[r.sourceCode]}</td><td>{stages[r.status]}</td>
          <td style={{overflowWrap:'anywhere'}}>{r.ownerId}</td>
          <td><button type="button" disabled={disabled} onClick={()=>{reset();onOpen(r.leadId);}}>Открыть найденную сделку</button></td>
        </tr>)}</tbody></table></div>}
      {position&&<button type="button" disabled={disabled} onClick={()=>void search()}>Первая страница поиска</button>}
      {data.nextCursor&&<button type="button" disabled={disabled} onClick={()=>void search(data.nextCursor)}>Следующая страница поиска</button>}
    </>}
  </section>;
}
