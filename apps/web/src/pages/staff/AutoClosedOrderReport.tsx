import React from 'react';
import {ClosedOrderComparison} from './ClosedOrderComparison';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const moneyKeys=['approvedBaseMinor','approvedExtraMinor','approvedTotalMinor',
  'simulatedPaidMinor','simulatedRefundedMinor'] as const;
type Amounts=Record<typeof moneyKeys[number],number>;
type Cash={id:string;kind:'SIM_PAYMENT'|'SIM_REFUND';amountMinor:number;revisionId:string};
type Row=Amounts&{workOrderId:string;closedDay:string;previewId:string;previewDigest:string;
  estimateRevisionId:string;postedEventId:string;ackEventId:string;closedEventId:string;cashEvents:Cash[]};
type Report={syntheticOnly:true;realMoney:false;releaseAllowed:false;marginAvailable:false;
  payrollAvailable:false;currency:'RUB';basis:string;timeZone:string;fromDay:string;toDay:string;
  summary:Amounts&{orderCount:number;cashEventCount:number};items:Row[]};
const int=(x:unknown):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0;
const money=(n:number)=>(n/100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
function parse(raw:unknown,from:string,to:string):Report{
  const r=raw as Report;
  if(!r||r.syntheticOnly!==true||r.realMoney!==false||r.releaseAllowed!==false||
    r.marginAvailable!==false||r.payrollAvailable!==false||r.currency!=='RUB'||
    r.basis!=='CLOSED_TECHNICAL_RECONCILED_PREVIEW_AND_CASH'||r.fromDay!==from||r.toDay!==to||
    typeof r.timeZone!=='string'||!r.timeZone||!r.summary||!Array.isArray(r.items)||
    r.items.length>200||r.summary.orderCount!==r.items.length)throw Error('contract');
  const totals=Object.fromEntries(moneyKeys.map(k=>[k,0])) as Amounts;
  const seen=new Set<string>(),events=new Set<string>();
  let n=0;
  for(const x of r.items){
    if(!x||![x.workOrderId,x.previewId,x.closedEventId,x.postedEventId,x.ackEventId,
      x.estimateRevisionId].every(id=>typeof id==='string'&&uuid.test(id))||
      !/^[a-f0-9]{64}$/.test(x.previewDigest)||!/^\d{4}-\d{2}-\d{2}$/.test(x.closedDay)||
      x.closedDay<from||x.closedDay>to||seen.has(x.workOrderId)||
      !moneyKeys.every(k=>int(x[k]))||!Array.isArray(x.cashEvents)||
      x.approvedBaseMinor+x.approvedExtraMinor!==x.approvedTotalMinor||
      x.simulatedPaidMinor-x.simulatedRefundedMinor!==x.approvedTotalMinor)throw Error('row');
    seen.add(x.workOrderId);
    let paid=0,refunded=0;
    for(const e of x.cashEvents){
      if(!e||typeof e.id!=='string'||!uuid.test(e.id)||events.has(e.id)||
        e.revisionId!==x.estimateRevisionId||!int(e.amountMinor)||e.amountMinor===0||
        !['SIM_PAYMENT','SIM_REFUND'].includes(e.kind)||++n>500)throw Error('event');
      events.add(e.id);
      if(e.kind==='SIM_PAYMENT')paid+=e.amountMinor;else refunded+=e.amountMinor;
    }
    if(paid!==x.simulatedPaidMinor||refunded!==x.simulatedRefundedMinor)throw Error('event totals');
    for(const key of moneyKeys)totals[key]+=x[key];
  }
  if(r.summary.cashEventCount!==n||!moneyKeys.every(k=>int(totals[k])&&totals[k]===r.summary[k]))
    throw Error('total');
  return r;
}

export function AutoClosedOrderReport({org,loc,from,to,allowed}:{
  org:string;loc:string;from:string;to:string;allowed:boolean
}){
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState('idle');
  const [exporting,setExporting]=React.useState(false);
  const active=React.useRef<AbortController|null>(null);
  const scope=org+':'+loc+':'+from+':'+to;
  React.useEffect(()=>{
    setReport(null);setStatus('idle');setExporting(false);
    return ()=>active.current?.abort();
  },[scope]);
  const url=(download=false)=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/reports/closed-orders'+(download?'/export':'')+
    '?'+new URLSearchParams({fromDay:from,toDay:to});
  async function load(download=false){
    if(!allowed)return;
    active.current?.abort();
    const pending=new AbortController();active.current=pending;
    if(download)setExporting(true);else{setReport(null);setStatus('loading');}
    try{
      const res=await fetch(url(download),{credentials:'include',cache:'no-store',signal:pending.signal});
      if(pending.signal.aborted)return;
      if([401,403,404].includes(res.status)){setReport(null);setStatus('denied');return;}
      if(res.status===409){setReport(null);setStatus('conflict');return;}
      if(res.status===422){setReport(null);setStatus('invalid');return;}
      if(!res.ok)throw Error('http');
      if(download){
        if(!res.headers.get('content-type')?.startsWith('text/csv'))throw Error('csv');
        const blob=await res.blob();if(pending.signal.aborted)return;
        const href=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=href;a.download='auto-closed-order-report.csv';
        document.body.appendChild(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(href),1000);
      }else{
        const data=parse(await res.json(),from,to);
        if(!pending.signal.aborted){setReport(data);setStatus('ready');}
      }
    }catch{if(!pending.signal.aborted){setReport(null);setStatus('error');}}
    finally{if(!pending.signal.aborted)setExporting(false);}
  }
  return <section aria-label="Реестр технически закрытых заказ-нарядов">
    <h2>Реестр технически закрытых заказ-нарядов</h2>
    <p>Период и филиал берутся из полей выше. Только тестовые закрытые документы:
      согласованные суммы, имитации оплат и возвратов, источники расчёта.
      Не выручка, не себестоимость, не прибыль и не зарплата.</p>
    <button type="button" disabled={!allowed||status==='loading'||exporting}
      onClick={()=>void load()}>Сверить закрытые заказ-наряды</button>
    {status==='loading'&&<p role="status">Проверка первичных событий…</p>}
    {status==='denied'&&<p role="alert">Доступ к реестру запрещён.</p>}
    {status==='conflict'&&<p role="alert">Реестр неполон или не сходится: сократите период или проверьте первичные документы.</p>}
    {status==='invalid'&&<p role="alert">Неверный период реестра.</p>}
    {status==='error'&&<p role="alert">Не удалось сверить реестр.</p>}
    {status==='ready'&&report&&<div>
      <p role="status">Закрытых заказов: {report.summary.orderCount}. Событий расчёта: {report.summary.cashEventCount}.
        Утверждено: {money(report.summary.approvedTotalMinor)} RUB (условно).
        Оплаты: {money(report.summary.simulatedPaidMinor)}; возвраты: {money(report.summary.simulatedRefundedMinor)}.
        Часовой пояс: {report.timeZone}.</p>
      <button type="button" disabled={exporting} onClick={()=>void load(true)}>Скачать реестр CSV</button>
      {!report.items.length&&<p>Нет технически закрытых заказов за период.</p>}
      <div style={{overflowX:'auto'}}><table>
        <caption>Сверка закрытых заказ-нарядов с первичными событиями</caption>
        <thead><tr><th scope="col">Заказ-наряд</th><th scope="col">День закрытия</th>
          <th scope="col">Основная смета</th><th scope="col">Допработы</th>
          <th scope="col">Имитации оплат</th><th scope="col">Имитации возвратов</th>
          <th scope="col">Источники</th></tr></thead>
        <tbody>{report.items.map(x=><tr key={x.workOrderId}>
          <th scope="row">{x.workOrderId}</th><td>{x.closedDay}</td>
          <td>{money(x.approvedBaseMinor)}</td><td>{money(x.approvedExtraMinor)}</td>
          <td>{money(x.simulatedPaidMinor)}</td><td>{money(x.simulatedRefundedMinor)}</td>
          <td><details><summary>Первичные события</summary>
            <p>Смета: {x.estimateRevisionId}</p><p>Пакет: {x.previewId}</p>
            <p>SHA-256: {x.previewDigest}</p><p>Проведение: {x.postedEventId}</p>
            <p>Подтверждение: {x.ackEventId}</p><p>Закрытие: {x.closedEventId}</p>
            {x.cashEvents.map(e=><p key={e.id}>{e.kind==='SIM_PAYMENT'?'Имитация оплаты':'Имитация возврата'}:
              {' '}{money(e.amountMinor)} · {e.id}</p>)}
          </details></td>
        </tr>)}</tbody>
      </table></div>
    </div>}
    <ClosedOrderComparison key={scope} org={org} loc={loc} from={from} to={to} allowed={allowed}/>
  </section>;
}
