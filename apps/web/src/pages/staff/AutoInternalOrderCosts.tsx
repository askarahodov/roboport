import React from 'react';
import {parseMaterialReport} from './AutoClosedOrderMaterials';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const id=(v:unknown):v is string=>typeof v==='string'&&uuid.test(v);
const int=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0;
const signed=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v);
const money=(n:number|null)=>n===null?'Не определено':(n/100).toLocaleString('ru-RU',{
  minimumFractionDigits:2,maximumFractionDigits:2,
})+' RUB';
const baseBasis='CLOSED_TECHNICAL_TRACED_MATERIAL_AND_APPROVED_INTERNAL_LABOR_COST';
type MaterialGroup=ReturnType<typeof parseMaterialReport>['items'][number]['groups'][number];
type Rate={id:string;version:number;rateMinorPerHour:number;proposedBy:string;createdAt:string;
  approvalId:string|null;approvedBy:string|null;approvedAt:string|null};
type Rates={syntheticOnly:true;payrollAvailable:false;currency:'RUB';items:Rate[];active:Rate|null};
type Labor={laborEntryId:string;serviceId:string;actualMinutes:number;
  status:'TRACED'|'NO_APPROVED_RATE'|'INACTIVE_MECHANIC';costMinor:number|null;
  source:{approvalId:string;revisionId:string;version:number;rateMinorPerHour:number}|null};
type Money={approvedTotalMinor:number;materialNetMinor:number|null;laborCostMinor:number|null;
  directCostMinor:number|null;syntheticDifferenceMinor:number|null};
type Order=Money&{workOrderId:string;closedDay:string;closedEventId:string;previewId:string;
  previewDigest:string;status:'COMPLETE'|'INCOMPLETE';laborEntries:Labor[];materialGroups:MaterialGroup[]};
type Report={syntheticOnly:true;realMoney:false;releaseAllowed:false;marginAvailable:false;
  payrollAvailable:false;currency:'RUB';basis:string;timeZone:string;fromDay:string;toDay:string;
  summary:Money&{orderCount:number;completeOrderCount:number;laborEntryCount:number};items:Order[]};
type Pending={url:string;body:string};
const keys=['approvedTotalMinor','materialNetMinor','laborCostMinor','directCostMinor','syntheticDifferenceMinor'] as const;
const sum=(values:(number|null)[])=>values.some(v=>v===null)?null:
  values.reduce<number>((total,v)=>total+(v??0),0);
function parseRates(raw:unknown):Rates{
  const r=raw as Rates;
  if(!r||r.syntheticOnly!==true||r.payrollAvailable!==false||r.currency!=='RUB'||
    !Array.isArray(r.items)||r.items.length>100)throw Error('rates');
  const seen=new Set<string>();
  for(const [i,p] of r.items.entries()){
    if(!p||!id(p.id)||seen.has(p.id)||p.version!==i+1||!int(p.rateMinorPerHour)||
      p.rateMinorPerHour>1e10||!id(p.proposedBy)||typeof p.createdAt!=='string'||
      (p.approvalId===null?(p.approvedBy!==null||p.approvedAt!==null||i!==r.items.length-1):
        (!id(p.approvalId)||!id(p.approvedBy)||p.approvedBy===p.proposedBy||
         typeof p.approvedAt!=='string')))throw Error('rate');
    seen.add(p.id);
  }
  const active=[...r.items].reverse().find(p=>p.approvalId!==null)??null;
  if(active===null?r.active!==null:(!r.active||r.active.id!==active.id||
    r.active.approvalId!==active.approvalId||r.active.rateMinorPerHour!==active.rateMinorPerHour))
    throw Error('active');
  return r;
}
function parseReport(raw:unknown,from:string,to:string):Report{
  const r=raw as Report;
  if(!r||r.syntheticOnly!==true||r.realMoney!==false||r.releaseAllowed!==false||
    r.marginAvailable!==false||r.payrollAvailable!==false||r.currency!=='RUB'||r.basis!==baseBasis||
    r.fromDay!==from||r.toDay!==to||typeof r.timeZone!=='string'||!r.timeZone||
    !r.summary||!Array.isArray(r.items)||r.items.length>200||r.summary.orderCount!==r.items.length)
    throw Error('report');
  const seen=new Set<string>();
  let count=0,complete=0;
  for(const row of r.items){
    if(!row||!id(row.workOrderId)||!id(row.closedEventId)||!id(row.previewId)||
      !/^[a-f0-9]{64}$/.test(row.previewDigest)||!int(row.approvedTotalMinor)||
      !Array.isArray(row.laborEntries)||!Array.isArray(row.materialGroups))throw Error('row');
    let laborComplete=row.laborEntries.length>0;
    for(const l of row.laborEntries){
      if(!l||!id(l.laborEntryId)||seen.has(l.laborEntryId)||!id(l.serviceId)||
        !int(l.actualMinutes)||l.actualMinutes<1||l.actualMinutes>1440||++count>500)
        throw Error('labor');
      seen.add(l.laborEntryId);
      if(l.status==='TRACED'){
        const s=l.source;
        if(!s||!id(s.approvalId)||!id(s.revisionId)||!int(s.version)||
          s.version<1||s.version>100||!int(s.rateMinorPerHour)||s.rateMinorPerHour>1e10||
          !int(l.costMinor)||Math.floor((l.actualMinutes*s.rateMinorPerHour+30)/60)!==l.costMinor)
          throw Error('cost');
      }else{
        if(!['NO_APPROVED_RATE','INACTIVE_MECHANIC'].includes(l.status)||l.source!==null||
          l.costMinor!==null)throw Error('unknown labor');
        laborComplete=false;
      }
    }
    const cost=laborComplete?sum(row.laborEntries.map(l=>l.costMinor)):null;
    if(cost!==row.laborCostMinor||(cost!==null&&!int(cost)))throw Error('labor total');
    const direct=cost===null||row.materialNetMinor===null?null:cost+row.materialNetMinor;
    const difference=direct===null?null:row.approvedTotalMinor-direct;
    if(row.status!==(direct===null?'INCOMPLETE':'COMPLETE')||row.directCostMinor!==direct||
      row.syntheticDifferenceMinor!==difference||
      (direct!==null&&(!int(direct)||!signed(difference))))throw Error('total');
    if(direct!==null)complete++;
  }
  // Повторно используем строгую проверку всей цепочки источников материалов.
  const materialItems=r.items.map(x=>({
    workOrderId:x.workOrderId,closedDay:x.closedDay,closedEventId:x.closedEventId,
    status:x.materialNetMinor===null?'INCOMPLETE':'COMPLETE',materialNetMinor:x.materialNetMinor,
    groups:x.materialGroups,
  }));
  parseMaterialReport({...r,basis:'CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE',
    items:materialItems,summary:{orderCount:r.items.length,
      completeOrderCount:materialItems.filter(x=>x.status==='COMPLETE').length,
      movementCount:materialItems.reduce((n,x)=>n+x.groups.reduce((m,g)=>m+g.movements.length,0),0),
      materialNetMinor:sum(materialItems.map(x=>x.materialNetMinor))}},from,to);
  if(r.summary.completeOrderCount!==complete||r.summary.laborEntryCount!==count||
    !keys.every(k=>r.summary[k]===sum(r.items.map(x=>x[k]))&&
      (r.summary[k]===null||signed(r.summary[k]))))throw Error('summary');
  return r;
}
export function AutoInternalOrderCosts({org,loc,from,to,allowed}:{
  org:string;loc:string;from:string;to:string;allowed:boolean
}){
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/locations/'+encodeURIComponent(loc);
  const policy=root+'/internal-labor-cost-policies';
  const reportPath=(exported=false)=>root+'/reports/internal-order-costs'+(exported?'/export':'')+
    '?'+new URLSearchParams({fromDay:from,toDay:to});
  const [rates,setRates]=React.useState<Rates|null>(null);
  const [rate,setRate]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [report,setReport]=React.useState<Report|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState('');
  const [message,setMessage]=React.useState('');
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  async function perform(url:string,body?:string,kind:'policy'|'report'|'export'='policy'){
    if(!allowed||busy)return;
    active.current?.abort();
    const controller=new AbortController();active.current=controller;
    setBusy(true);setError('');setMessage('');
    if(kind==='report')setReport(null);
    if(kind==='policy'&&!body)setRates(null);
    try{
      let headers:Record<string,string>|undefined;
      if(body){
        const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',
          cache:'no-store',signal:controller.signal});
        if(!csrf.ok)throw Error('csrf');
        const token=await csrf.json() as {csrfToken?:string};
        if(typeof token.csrfToken!=='string')throw Error('csrf');
        headers={'Content-Type':'application/json','X-CSRF-Token':token.csrfToken};
      }
      const res=await fetch(url,{method:body?'POST':'GET',body,headers,credentials:'include',
        cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404,409,422].includes(res.status)){
        setReport(null);setRates(null);setPending(null);
        setError([401,403,404].includes(res.status)?
          'Доступ к внутренним затратам запрещён либо требуется другой утверждающий менеджер.':
          res.status===409?'Версия, источники или объём отчёта конфликтуют. Перечитайте данные.':
          'Некорректные параметры расчёта.');return;
      }
      if(!res.ok)throw Error('http');
      if(kind==='export'){
        if(!res.headers.get('content-type')?.startsWith('text/csv'))throw Error('csv');
        const blob=await res.blob();if(controller.signal.aborted)return;
        const href=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=href;a.download='auto-internal-order-costs.csv';
        document.body.appendChild(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(href),1000);
      }else if(kind==='report'){
        const data=parseReport(await res.json(),from,to);
        if(!controller.signal.aborted)setReport(data);
      }else{
        const data=parseRates(await res.json());
        if(!controller.signal.aborted){setRates(data);setPending(null);if(body){
          setReport(null);setMessage('Политика сохранена. Прежние записи труда не изменены.');
        }}
      }
    }catch{
      if(!controller.signal.aborted){
        setReport(null);setRates(null);
        if(body)setPending({url,body});
        setError(body?'Результат записи неизвестен. Повторите с тем же ключом или перечитайте ставки.':
          'Не удалось проверить данные внутренних затрат.');
      }
    }finally{if(!controller.signal.aborted)setBusy(false);}
  }
  const canPropose=!!rates&&!pending&&!busy&&rates.items.length<100&&
    !rates.items.some(p=>p.approvalId===null)&&/^(0|[1-9][0-9]*)$/.test(rate)&&
    int(Number(rate))&&Number(rate)<=1e10;
  return <section aria-label="Внутренние затраты закрытых заказов">
    <h2>Внутренние затраты закрытых заказов</h2>
    <p>Только synthetic-dev. Внутренняя ставка филиала не является тарифом продажи или
      зарплатой. Условная разница = согласованная сумма − подтверждённые материалы − расчётный труд.
      Это не прибыль, не реальная выручка и не payroll.</p>
    <button type="button" disabled={!allowed||busy} onClick={()=>void perform(policy)}>
      Прочитать внутренние ставки</button>
    {rates&&<div>
      <p>Активная ставка: {rates.active?money(rates.active.rateMinorPerHour)+' / час':'Не утверждена'}.</p>
      <ul>{rates.items.map(p=><li key={p.id}>Версия {p.version} · {money(p.rateMinorPerHour)} / час ·
        {p.approvalId?' Утверждена':' Ожидает независимого утверждения'}
        {!p.approvalId&&!pending&&<button type="button" disabled={busy} onClick={()=>void perform(
          policy+'/'+p.id+'/approve',JSON.stringify({requestKey:crypto.randomUUID()}))}>
          Утвердить внутреннюю ставку версии {p.version}</button>}
      </li>)}</ul>
      <form onSubmit={e=>{e.preventDefault();if(canPropose)void perform(policy,JSON.stringify({
        requestKey:crypto.randomUUID(),expectedVersion:rates.items.length,rateMinorPerHour:Number(rate),
      }));}}>
        <label>Расчётная ставка, копеек за час
          <input type="number" min="0" max="10000000000" step="1"
            value={rate} onChange={e=>setRate(e.target.value)} aria-label="Внутренняя ставка, копеек за час"/>
        </label>
        <button type="submit" disabled={!canPropose}>Предложить внутреннюю ставку</button>
      </form>
    </div>}
    {pending&&<button type="button" disabled={busy}
      onClick={()=>void perform(pending.url,pending.body)}>Повторить запись ставки с тем же ключом</button>}
    <button type="button" disabled={!allowed||busy} onClick={()=>void perform(reportPath(),undefined,'report')}>
      Рассчитать внутренние затраты</button>
    {busy&&<p role="status">Проверка внутренних затрат…</p>}
    {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    {report&&<div>
      <p role="status">Полная оценка: {report.summary.completeOrderCount} из {report.summary.orderCount}.
        Прямые затраты: {money(report.summary.directCostMinor)}.
        Условная разница: {money(report.summary.syntheticDifferenceMinor)}.</p>
      <button type="button" disabled={busy} onClick={()=>void perform(reportPath(true),undefined,'export')}>
        Скачать внутренние затраты CSV</button>
      {!report.items.length&&<p>Закрытых заказов за период нет.</p>}
      <div style={{overflowX:'auto'}}><table>
        <caption>Внутренние затраты и первичные источники</caption>
        <thead><tr><th scope="col">Заказ-наряд</th><th scope="col">День</th>
          <th scope="col">Согласовано</th><th scope="col">Материалы</th>
          <th scope="col">Труд</th><th scope="col">Прямые затраты</th>
          <th scope="col">Условная разница</th><th scope="col">Первичка</th></tr></thead>
        <tbody>{report.items.map(row=><tr key={row.workOrderId}>
          <th scope="row">{row.workOrderId}</th><td>{row.closedDay}</td>
          <td>{money(row.approvedTotalMinor)}</td><td>{money(row.materialNetMinor)}</td>
          <td>{money(row.laborCostMinor)}</td><td>{money(row.directCostMinor)}</td>
          <td>{money(row.syntheticDifferenceMinor)}</td>
          <td><details><summary>Источники затрат</summary>
            <p>Закрытие: {row.closedEventId} · Пакет: {row.previewId} · SHA-256: {row.previewDigest}</p>
            {row.laborEntries.map(l=><p key={l.laborEntryId}>Труд: {l.laborEntryId} ·
              Услуга: {l.serviceId} · {l.actualMinutes} мин · {money(l.costMinor)} ·
              {l.source?' Ставка '+l.source.revisionId+' · Утверждение '+l.source.approvalId+
                ' · '+money(l.source.rateMinorPerHour)+'/час':
                l.status==='INACTIVE_MECHANIC'?' Допуск механика отозван':' Нет утверждённой ставки'}</p>)}
            {row.materialGroups.map(g=><div key={g.skuId+':'+g.lotId}>
              <p>Партия: {g.lotId||'Не установлена'} · SKU: {g.skuId} ·
                Расход: {g.netQuantity} · {money(g.materialNetMinor)}</p>
              {g.source&&<p>Приход: {g.source.receiptId} · Приёмка: {g.source.acceptanceId} ·
                Оценка: {g.source.valuationId} · {money(g.source.unitNetMinor)}/ед.</p>}
              {g.fifoSlices.map((s,i)=><p key={s.stockEventId+':'+i}>{s.serialId?'Серийная деталь':'FIFO'}: {s.action==='ISSUE'?'Выдача':'Возврат'} ·
                {s.quantity} × {money(s.unitNetMinor)} · Приход: {s.receiptId} · Приёмка: {s.acceptanceId}
                {' '}· Оценка: {s.valuationId} · Исходная выдача: {s.originalIssueId}
                {s.serialId&&' · Серийная запись: '+s.serialId+' · Подтверждение: '+s.serialApprovalId}
                {s.sourceLocationId&&' · Филиал закупки: '+s.sourceLocationId}</p>)}
              {g.movements.map(m=><p key={m.stockEventId}>{m.action==='ISSUE'?'Выдача':'Возврат'}:
                {m.quantity} · {m.stockEventId} · {m.lotMovementId||'Без партии'}</p>)}
            </div>)}
          </details></td>
        </tr>)}</tbody>
      </table></div>
    </div>}
  </section>;
}
