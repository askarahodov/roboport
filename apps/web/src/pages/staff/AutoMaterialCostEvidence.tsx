import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const today=()=>new Date().toISOString().slice(0,10);
const integer=(n:unknown):n is number=>typeof n==='number'&&Number.isSafeInteger(n)&&n>=0;
const enums=['NO_RECEIPT','REVIEW_REQUIRED','SINGLE_VALUED_RECEIPT'] as const;
type Kind=typeof enums[number];
type Item={
  workOrderId:string;lotId:string;skuId:string;
  grossIssued:number;grossReturned:number;netPeriod:number;
  receiptCount:number;acceptedReceiptCount:number;valuedReceiptCount:number;
  quoteEvidence:Kind;quoteValuationId:string|null;quoteCurrency:string|null;
  quoteUnitNetMinor:string|null;movementCount:number;movementIds:string[];
};
type Report={
  syntheticOnly:true;realMoney:false;marginAvailable:false;releaseAllowed:false;
  basis:'IMMUTABLE_LOT_MOVEMENTS_RECEIPT_QUOTE_PROVENANCE';
  timeZone:string;fromDay:string;toDay:string;priceIsOnlyReceiptQuote:true;
  summary:{workOrderCount:number;lotCount:number;movementCount:number;
    grossIssued:number;grossReturned:number;netPeriod:number;
    evidenceCounts:Record<Kind,number>};
  items:Item[];
};
const names:Record<Kind,string>={
  NO_RECEIPT:'Документ прихода отсутствует',
  REVIEW_REQUIRED:'Требуется проверка приходов/оценок',
  SINGLE_VALUED_RECEIPT:'Есть единственная оценённая приёмка (не себестоимость выдачи)',
};
function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('bad contract');
  const data=raw as Partial<Report>;
  if(data.syntheticOnly!==true||data.realMoney!==false||
     data.marginAvailable!==false||data.releaseAllowed!==false||
     data.priceIsOnlyReceiptQuote!==true||
     data.basis!=='IMMUTABLE_LOT_MOVEMENTS_RECEIPT_QUOTE_PROVENANCE'||
     data.fromDay!==from||data.toDay!==to||
     typeof data.timeZone!=='string'||!data.timeZone||data.timeZone.length>100||
     !data.summary||!Array.isArray(data.items)||data.items.length>500)throw Error('invalid');
  const summary=data.summary,items=data.items;
  if(![summary.workOrderCount,summary.lotCount,summary.movementCount,
    summary.grossIssued,summary.grossReturned].every(integer)||
    !Number.isSafeInteger(summary.netPeriod)||!summary.evidenceCounts||
    !enums.every(kind=>integer(summary.evidenceCounts[kind])))throw Error('invalid');
  const workOrders=new Set<string>(),lots=new Set<string>(),pairs=new Set<string>();
  const counts:Record<Kind,number>={
    NO_RECEIPT:0,REVIEW_REQUIRED:0,SINGLE_VALUED_RECEIPT:0,
  };
  let issued=0,returned=0,movements=0;
  for(const x of items){
    if(!x||!uuid.test(x.workOrderId)||!uuid.test(x.lotId)||!uuid.test(x.skuId)||
       ![x.grossIssued,x.grossReturned,x.receiptCount,x.acceptedReceiptCount,
         x.valuedReceiptCount,x.movementCount].every(integer)||
       !Number.isSafeInteger(x.netPeriod)||x.netPeriod!==x.grossIssued-x.grossReturned||
       x.acceptedReceiptCount>x.receiptCount||x.valuedReceiptCount>x.acceptedReceiptCount||
       !enums.includes(x.quoteEvidence)||!Array.isArray(x.movementIds)||
       x.movementIds.length!==x.movementCount||x.movementCount<1||
       !x.movementIds.every(id=>typeof id==='string'&&uuid.test(id))||
       pairs.has(x.workOrderId+':'+x.lotId))throw Error('invalid row');
    if(x.quoteEvidence==='SINGLE_VALUED_RECEIPT'){
      if(x.receiptCount!==1||x.acceptedReceiptCount!==1||x.valuedReceiptCount!==1||
         !x.quoteValuationId||!uuid.test(x.quoteValuationId)||
         !x.quoteCurrency||!/^[A-Z]{3}$/.test(x.quoteCurrency)||
         !x.quoteUnitNetMinor||!/^(0|[1-9]\d{0,15})$/.test(x.quoteUnitNetMinor))
        throw Error('bad quote');
    }else if(x.quoteValuationId!==null||x.quoteCurrency!==null||
             x.quoteUnitNetMinor!==null)throw Error('leaked quote');
    if((x.quoteEvidence==='NO_RECEIPT')!==(x.receiptCount===0))throw Error('bad kind');
    if(x.quoteEvidence==='REVIEW_REQUIRED'&&x.receiptCount===1&&
       x.acceptedReceiptCount===1&&x.valuedReceiptCount===1)throw Error('bad kind');
    pairs.add(x.workOrderId+':'+x.lotId);workOrders.add(x.workOrderId);lots.add(x.lotId);
    counts[x.quoteEvidence]++;issued+=x.grossIssued;returned+=x.grossReturned;
    movements+=x.movementCount;
  }
  if(workOrders.size!==summary.workOrderCount||lots.size!==summary.lotCount||
     movements!==summary.movementCount||issued!==summary.grossIssued||
     returned!==summary.grossReturned||issued-returned!==summary.netPeriod||
     movements>500||!Number.isSafeInteger(issued)||!Number.isSafeInteger(returned)||
     !enums.every(kind=>counts[kind]===summary.evidenceCounts[kind]))
    throw Error('bad totals');
  return data as Report;
}
export function AutoMaterialCostEvidence(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today);
  const [to,setTo]=React.useState(today);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'limit'|'error'>('idle');
  const [exporting,setExporting]=React.useState(false);
  const [exportError,setExportError]=React.useState('');
  const active=React.useRef<AbortController|null>(null);
  const exportActive=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{active.current?.abort();exportActive.current?.abort();},[]);
  const reset=()=>{
    active.current?.abort();exportActive.current?.abort();
    setReport(null);setStatus('idle');setExporting(false);setExportError('');
  };
  const days=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(days)&&days>=1&&days<=31;
  const url=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/reports/material-cost-evidence?'+
    new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    active.current?.abort();
    const pending=new AbortController();active.current=pending;
    setReport(null);setStatus('loading');setExportError('');
    try{
      const response=await fetch(url(),{credentials:'include',
        cache:'no-store',signal:pending.signal});
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===409){setStatus('limit');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(!response.ok)throw Error('http');
      const value=parse(await response.json(),from,to);
      if(!pending.signal.aborted){setReport(value);setStatus('ready');}
    }catch{if(!pending.signal.aborted)setStatus('error');}
  }
  async function exportCsv(){
    if(!report||status!=='ready'||exporting)return;
    exportActive.current?.abort();
    const pending=new AbortController();exportActive.current=pending;
    setExporting(true);setExportError('');
    try{
      const response=await fetch(
        url().replace('/reports/material-cost-evidence?','/reports/material-cost-evidence/export?'),{
          credentials:'include',cache:'no-store',signal:pending.signal,
        });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setReport(null);setStatus('denied');return;
      }
      if(response.status===409){setReport(null);setStatus('limit');return;}
      if(response.status===422){setReport(null);setStatus('invalid');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('bad CSV');
      const blob=await response.blob();
      if(pending.signal.aborted)return;
      const objectUrl=URL.createObjectURL(blob);
      const anchor=document.createElement('a');anchor.href=objectUrl;
      anchor.download='auto-material-cost-evidence.csv';
      document.body.appendChild(anchor);anchor.click();anchor.remove();
      window.setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
    }catch{if(!pending.signal.aborted)setExportError('Не удалось скачать CSV.');}
    finally{if(!pending.signal.aborted)setExporting(false);}
  }
  return <section aria-label="Аудит закупочной первички (dev)">
    <h1>Проверка происхождения стоимости материалов</h1>
    <p><strong>Только synthetic dev.</strong> Отчёт сравнивает неизменяемые
      выдачи/возвраты партий на заказ-наряды с принятыми документами
      прихода и их оценками. Даже единственная оценённая приёмка
      <strong> не доказывает себестоимость конкретной выдачи</strong>:
      партии могут иметь иной источник запаса. Это не прибыль,
      не COGS, не НДС и не бухгалтерский документ.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода"
      value={from} onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода"
      value={to} onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Проверить документы материалов</button>
    {!valid&&<p>Укажите UUID своей организации/филиала и период 1–31 день.</p>}
    {status==='loading'&&<p role="status">Сверка складских событий и приходов…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён: требуются MANAGER и разрешения на документы склада/цены.</p>}
    {status==='invalid'&&<p role="alert">Проверьте период и идентификаторы.</p>}
    {status==='limit'&&<p role="alert">Более 500 событий: сократите период; частичные данные не выдаются.</p>}
    {status==='error'&&<p role="alert">Не удалось сформировать аудит. Повторите запрос.</p>}
    {exportError&&<p role="alert">{exportError}</p>}
    {status==='ready'&&report&&<section aria-label="Результаты аудита материалов">
      <p role="status">Заказ-нарядов: {report.summary.workOrderCount}.
        Партий: {report.summary.lotCount}.
        Событий: {report.summary.movementCount}.
        Выдано: {report.summary.grossIssued}.
        Возвращено: {report.summary.grossReturned}.</p>
      <p>Часовой пояс: {report.timeZone}.
        Без приходов: {report.summary.evidenceCounts.NO_RECEIPT}.
        Требуется проверка: {report.summary.evidenceCounts.REVIEW_REQUIRED}.
        Единственная оценённая приёмка: {report.summary.evidenceCounts.SINGLE_VALUED_RECEIPT}.
        Закупочная цена — только справка из первичного документа,
        не стоимость расхода конкретной детали.</p>
      <button type="button" disabled={exporting}
        onClick={()=>void exportCsv()}>{exporting?'Готовим CSV…':'Скачать аудит CSV'}</button>
      {!report.items.length&&<p>Складских выдач и возвратов за период нет.</p>}
      <div style={{overflowX:'auto'}}><table>
        <caption>Движения партий и подтверждённость закупочной первички</caption>
        <thead><tr><th scope="col">Заказ-наряд UUID</th>
          <th scope="col">Партия UUID</th><th scope="col">Выдано</th>
          <th scope="col">Возвращено</th><th scope="col">Разница</th>
          <th scope="col">Документы прихода</th>
          <th scope="col">Подтверждение</th></tr></thead>
        <tbody>{report.items.map(item=><tr key={item.workOrderId+':'+item.lotId}>
          <th scope="row">{item.workOrderId}</th><td>{item.lotId}</td>
          <td>{item.grossIssued}</td><td>{item.grossReturned}</td>
          <td>{item.netPeriod}</td><td>{item.receiptCount}</td>
          <td>{names[item.quoteEvidence]}</td>
        </tr>)}</tbody>
      </table></div>
    </section>}
  </section>;
}
