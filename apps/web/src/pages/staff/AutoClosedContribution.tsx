import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const BASIS='CLOSED_TECHNICAL_QC_POSTED_SIM_SETTLEMENT_AND_PROVEN_DIRECT_COSTS';
type MoneyKey='approvedTotalMinor'|'simulatedPaidMinor'|'simulatedRefundedMinor'|
  'simulatedNetMinor'|'materialNetMinor'|'laborCostMinor'|'directCostMinor'|
  'syntheticDifferenceMinor';
const MONEY:MoneyKey[]=['approvedTotalMinor','simulatedPaidMinor','simulatedRefundedMinor',
  'simulatedNetMinor','materialNetMinor','laborCostMinor','directCostMinor',
  'syntheticDifferenceMinor'];
type Cash={id:string;kind:'SIM_PAYMENT'|'SIM_REFUND';amountMinor:number;revisionId:string};
type Labor={laborEntryId:string;serviceId:string;actualMinutes:number;
  status:'TRACED'|'NO_APPROVED_RATE'|'INACTIVE_MECHANIC';costMinor:number|null;
  source:{approvalId:string;revisionId:string;version:number;rateMinorPerHour:number}|null};
type Move={stockEventId:string;action:'ISSUE'|'RETURN';quantity:number;lotMovementId:string|null};
type Group={skuId:string;lotId:string|null;status:string;materialNetMinor:number|null;
  source:{valuationId:string}|null;movements:Move[]};
type Order=Record<MoneyKey,number|null>&{
  workOrderId:string;closedDay:string;closedEventId:string;postedEventId:string;
  ackEventId:string;previewId:string;previewDigest:string;estimateRevisionId:string;
  status:'COMPLETE'|'INCOMPLETE';cashEvents:Cash[];
  laborEntries:Labor[];materialGroups:Group[];
};
type Day=Record<MoneyKey,number|null>&{
  localDay:string;orderCount:number;completeOrderCount:number;
  incompleteOrderCount:number;simCashEventCount:number;
};
type Summary=Record<MoneyKey,number|null>&{
  orderCount:number;completeOrderCount:number;incompleteOrderCount:number;
  simCashEventCount:number;laborEntryCount:number;stockEventCount:number;
};
type Report={
  syntheticOnly:true;realMoney:false;releaseAllowed:false;marginAvailable:false;
  payrollAvailable:false;currency:'RUB';basis:string;timeZone:string;
  fromDay:string;toDay:string;summary:Summary;days:Day[];items:Order[];
};
const int=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v);
const nonneg=(v:unknown):v is number=>int(v)&&v>=0;
const money=(v:unknown,nullable=false):v is number|null=>
  (nullable&&v===null)||int(v);
const today=()=>new Date().toISOString().slice(0,10);
const format=(v:number|null)=>
  v===null?'Не определено':(v/100).toLocaleString('ru-RU',{
    minimumFractionDigits:2,maximumFractionDigits:2,
  })+' RUB (SIM)';
const sum=(xs:number[])=>xs.reduce((a,b)=>a+b,0);
function parse(raw:unknown,from:string,to:string):Report{
  const r=raw as Report;
  const length=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  if(!r||r.syntheticOnly!==true||r.realMoney!==false||
    r.releaseAllowed!==false||r.marginAvailable!==false||
    r.payrollAvailable!==false||r.currency!=='RUB'||r.basis!==BASIS||
    r.fromDay!==from||r.toDay!==to||typeof r.timeZone!=='string'||
    !r.summary||!Array.isArray(r.days)||r.days.length!==length||
    !Array.isArray(r.items)||r.items.length>200)throw Error('contract');
  const expectedDays=Array.from({length},(_,i)=>new Date(
    Date.parse(from+'T00:00:00Z')+i*86400000).toISOString().slice(0,10));
  const dayMap=new Map<string,Day>();
  const ids=new Set<string>(),cashIds=new Set<string>(),laborIds=new Set<string>(),
    stockIds=new Set<string>();
  for(const d of r.days){
    if(!d||!expectedDays.includes(d.localDay)||dayMap.has(d.localDay)||
      !nonneg(d.orderCount)||!nonneg(d.completeOrderCount)||
      !nonneg(d.incompleteOrderCount)||!nonneg(d.simCashEventCount)||
      d.orderCount!==d.completeOrderCount+d.incompleteOrderCount||
      !MONEY.every(k=>money(d[k],k==='materialNetMinor'||k==='laborCostMinor'||
        k==='directCostMinor'||k==='syntheticDifferenceMinor')))
      throw Error('day');
    if(d.simulatedNetMinor!==d.approvedTotalMinor||
      d.simulatedPaidMinor!-d.simulatedRefundedMinor!!==d.simulatedNetMinor||
      (d.incompleteOrderCount>0?
        [d.materialNetMinor,d.laborCostMinor,d.directCostMinor,
         d.syntheticDifferenceMinor].some(v=>v!==null):
        (d.materialNetMinor===null||d.laborCostMinor===null||
         d.directCostMinor===null||d.syntheticDifferenceMinor===null||
         d.approvedTotalMinor!-d.directCostMinor!==d.syntheticDifferenceMinor||
         d.materialNetMinor+d.laborCostMinor!==d.directCostMinor)))
      throw Error('daily totals');
    dayMap.set(d.localDay,d);
  }
  if(dayMap.size!==length)throw Error('missing day');
  const aggregate=new Map(expectedDays.map(s=>[s,{
    orderCount:0,completeOrderCount:0,incompleteOrderCount:0,simCashEventCount:0,
    approvedTotalMinor:0,simulatedPaidMinor:0,simulatedRefundedMinor:0,
    simulatedNetMinor:0,materialNetMinor:0 as number|null,
    laborCostMinor:0 as number|null,directCostMinor:0 as number|null,
    syntheticDifferenceMinor:0 as number|null,
  }]));
  for(const x of r.items){
    if(!x||!uuid.test(x.workOrderId)||ids.has(x.workOrderId)||
      ![x.closedEventId,x.postedEventId,x.ackEventId,x.previewId,
        x.estimateRevisionId].every(k=>typeof k==='string'&&uuid.test(k))||
      !/^[a-f0-9]{64}$/i.test(x.previewDigest)||
      !aggregate.has(x.closedDay)||
      !['COMPLETE','INCOMPLETE'].includes(x.status)||
      !MONEY.every(k=>money(x[k],k==='materialNetMinor'||k==='laborCostMinor'||
        k==='directCostMinor'||k==='syntheticDifferenceMinor'))||
      !Array.isArray(x.cashEvents)||
      (x.cashEvents.length===0 && (x.approvedTotalMinor!==0||
       x.simulatedPaidMinor!==0||x.simulatedRefundedMinor!==0))||
      !Array.isArray(x.laborEntries)||!Array.isArray(x.materialGroups))
      throw Error('order');
    ids.add(x.workOrderId);
    const cashIn:number[]=[],cashOut:number[]=[];
    for(const e of x.cashEvents){
      if(!e||!uuid.test(e.id)||cashIds.has(e.id)||!uuid.test(e.revisionId)||
        e.revisionId!==x.estimateRevisionId||
        !['SIM_PAYMENT','SIM_REFUND'].includes(e.kind)||!nonneg(e.amountMinor)||
        e.amountMinor===0)throw Error('cash source');
      cashIds.add(e.id);
      (e.kind==='SIM_PAYMENT'?cashIn:cashOut).push(e.amountMinor);
    }
    if(x.simulatedPaidMinor!==sum(cashIn)||
      x.simulatedRefundedMinor!==sum(cashOut)||
      x.simulatedNetMinor!==x.simulatedPaidMinor!-x.simulatedRefundedMinor!||
      x.simulatedNetMinor!==x.approvedTotalMinor)throw Error('settlement');
    for(const labor of x.laborEntries){
      if(!labor||!uuid.test(labor.laborEntryId)||laborIds.has(labor.laborEntryId)||
        !uuid.test(labor.serviceId)||!nonneg(labor.actualMinutes)||
        !['TRACED','NO_APPROVED_RATE','INACTIVE_MECHANIC'].includes(labor.status)||
        !money(labor.costMinor,true))throw Error('labor source');
      if(labor.status==='TRACED'?
        (!labor.source||!uuid.test(labor.source.approvalId)||labor.costMinor===null):
        (labor.source!==null||labor.costMinor!==null))throw Error('labor provenance');
      laborIds.add(labor.laborEntryId);
    }
    for(const group of x.materialGroups){
      if(!group||!uuid.test(group.skuId)||
        (group.lotId!==null&&!uuid.test(group.lotId))||
        typeof group.status!=='string'||!/^[A-Z_]{2,40}$/.test(group.status)||
        !money(group.materialNetMinor,true)||!Array.isArray(group.movements))
        throw Error('material group');
      for(const ev of group.movements){
        if(!ev||!uuid.test(ev.stockEventId)||stockIds.has(ev.stockEventId)||
          !['ISSUE','RETURN'].includes(ev.action)||!nonneg(ev.quantity)||
          (ev.lotMovementId!==null&&!uuid.test(ev.lotMovementId)))
          throw Error('stock source');
        stockIds.add(ev.stockEventId);
      }
    }
    const complete=x.status==='COMPLETE';
    if(complete!==[x.materialNetMinor,x.laborCostMinor,
      x.directCostMinor,x.syntheticDifferenceMinor].every(v=>v!==null))
      throw Error('completeness');
    if(complete&&(x.materialNetMinor!+x.laborCostMinor!!==x.directCostMinor||
      x.approvedTotalMinor!-x.directCostMinor!!==x.syntheticDifferenceMinor))
      throw Error('difference');
    const d=aggregate.get(x.closedDay)!;
    d.orderCount++;d[complete?'completeOrderCount':'incompleteOrderCount']++;
    d.simCashEventCount+=x.cashEvents.length;
    for(const key of MONEY){
      if(key==='materialNetMinor'||key==='laborCostMinor'||
        key==='directCostMinor'||key==='syntheticDifferenceMinor'){
        // An incomplete order invalidates *all* day-level cost totals,
        // including an individually traceable material amount. The
        // server must never publish a partial cost as period profit.
        d[key]=!complete||d[key]===null||x[key]===null
          ?null:d[key]!+x[key]!;
      }else{d[key]=d[key]!+x[key]!;}
    }
  }
  for(const [day,expected] of aggregate.entries()){
    const d=dayMap.get(day)!;
    if(d.orderCount!==expected.orderCount||
      d.completeOrderCount!==expected.completeOrderCount||
      d.incompleteOrderCount!==expected.incompleteOrderCount||
      d.simCashEventCount!==expected.simCashEventCount||
      !MONEY.every(k=>d[k]===expected[k]))throw Error('source reconciliation');
  }
  const s=r.summary;
  if(!nonneg(s.orderCount)||!nonneg(s.completeOrderCount)||
    !nonneg(s.incompleteOrderCount)||!nonneg(s.simCashEventCount)||
    !nonneg(s.laborEntryCount)||!nonneg(s.stockEventCount)||
    s.orderCount!==r.items.length||s.completeOrderCount!==r.items.filter(
      o=>o.status==='COMPLETE').length||
    s.incompleteOrderCount!==s.orderCount-s.completeOrderCount||
    s.simCashEventCount!==cashIds.size||s.laborEntryCount!==laborIds.size||
    s.stockEventCount!==stockIds.size||
    !MONEY.every(k=>money(s[k],true) &&
      (r.days.some(d=>d[k]===null)?s[k]===null:
        s[k]===sum(r.days.map(d=>d[k]!)))))throw Error('summary');
  return r;
}

export function AutoClosedContribution(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today),[to,setTo]=React.useState(today);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|
    'invalid'|'limit'|'error'>('idle');
  const [data,setData]=React.useState<Report|null>(null);
  const [exporting,setExporting]=React.useState(false);
  const request=React.useRef<AbortController|null>(null);
  const download=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{request.current?.abort();download.current?.abort();},[]);
  function reset(){
    request.current?.abort();download.current?.abort();
    setData(null);setStatus('idle');setExporting(false);
  }
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  const route=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/reports/closed-contribution';
  const query=()=>'?'+new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    request.current?.abort();download.current?.abort();
    const ctrl=new AbortController();request.current=ctrl;
    setData(null);setStatus('loading');
    try{
      const res=await fetch(route()+query(),{credentials:'include',cache:'no-store',
        signal:ctrl.signal});
      if(ctrl.signal.aborted)return;
      if([401,403,404].includes(res.status)){setStatus('denied');return;}
      if(res.status===409){setStatus('limit');return;}
      if(res.status===422){setStatus('invalid');return;}
      if(!res.ok)throw Error('http');
      const parsed=parse(await res.json(),from,to);
      if(!ctrl.signal.aborted){setData(parsed);setStatus('ready');}
    }catch{if(!ctrl.signal.aborted)setStatus('error');}
  }
  async function exportCsv(){
    if(status!=='ready'||!data||exporting)return;
    download.current?.abort();
    const ctrl=new AbortController();download.current=ctrl;
    setExporting(true);
    try{
      const res=await fetch(route()+'/export'+query(),{
        credentials:'include',cache:'no-store',signal:ctrl.signal,
      });
      if(ctrl.signal.aborted)return;
      if([401,403,404].includes(res.status)){
        setData(null);setStatus('denied');return;
      }
      if(res.status===409){setData(null);setStatus('limit');return;}
      if(res.status===422){setData(null);setStatus('invalid');return;}
      if(!res.ok||!res.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('csv');
      const blob=await res.blob();
      if(blob.size<=0||blob.size>1024*1024)throw Error('csv bounds');
      if(ctrl.signal.aborted)return;
      const href=URL.createObjectURL(blob);
      const link=document.createElement('a');
      link.href=href;link.download='auto-closed-contribution.csv';
      document.body.appendChild(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(href),1000);
    }catch{
      if(!ctrl.signal.aborted){setData(null);setStatus('error');}
    }finally{if(download.current===ctrl)setExporting(false);}
  }
  return <section aria-label="Сверка закрытых заказ-нарядов и прямых затрат (dev)"
      style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}>
    <h1>Закрытые заказ-наряды: расчёты и подтверждённые затраты</h1>
    <p><strong>Только synthetic current-dev.</strong> Это не бухгалтерская
      прибыль, реальная выручка, себестоимость по учётным стандартам,
      зарплата или основание выдачи автомобиля. Сверка включает только
      заказы с независимым QC PASS, проведённым и подтверждённым актом
      и техническим закрытием; расчёты только SIM. Разница между
      согласованной суммой и доказанными прямыми затратами —
      условный показатель, не margin. Если источник материалов/труда
      отсутствует, разница дня и всего периода <strong>не определена</strong>.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода" value={from}
      onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода" value={to}
      onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Сверить закрытые заказ-наряды</button>
    {!valid&&<p>Укажите свою организацию, филиал и период 1–31 день.</p>}
    {status==='loading'&&<p role="status">Проверяем документы и первичные источники…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или отозван. Сводка очищена.</p>}
    {status==='invalid'&&<p role="alert">Неверный период. Допустимо от 1 до 31 дня.</p>}
    {status==='limit'&&<p role="alert">Конфликт снимка или превышен лимит первички.
      Сократите период и повторите.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить согласованность источников.
      Старая сводка скрыта.</p>}
    {data&&status==='ready'&&<section aria-label="Результаты сверки закрытых заказ-нарядов"
        style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}>
      <p role="status">Закрытых заказов: {data.summary.orderCount};
        с полными источниками: {data.summary.completeOrderCount};
        без полного доказательства: {data.summary.incompleteOrderCount};
        согласовано: {format(data.summary.approvedTotalMinor)};
        условно оплачено: {format(data.summary.simulatedNetMinor)};
        прямые затраты: {format(data.summary.directCostMinor)};
        условная разница: {format(data.summary.syntheticDifferenceMinor)}.</p>
      <div role="region" aria-label="Прокручиваемые дневные итоги" tabIndex={0}
          style={{maxWidth:'100%',minWidth:0,overflowX:'auto'}}>
        <table aria-label="Период закрытия и доказанные суммы">
          <thead><tr><th>Локальный день</th><th>Заказы</th><th>Неполные</th>
            <th>Согласовано</th><th>SIM баланс</th><th>Материалы</th>
            <th>Труд</th><th>Прямые затраты</th><th>Условная разница</th></tr></thead>
          <tbody>{data.days.map(d=><tr key={d.localDay}>
            <td>{d.localDay}</td><td>{d.orderCount}</td>
            <td>{d.incompleteOrderCount}</td>
            <td>{format(d.approvedTotalMinor)}</td>
            <td>{format(d.simulatedNetMinor)}</td>
            <td>{format(d.materialNetMinor)}</td>
            <td>{format(d.laborCostMinor)}</td>
            <td>{format(d.directCostMinor)}</td>
            <td>{format(d.syntheticDifferenceMinor)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {data.items.length===0&&<p>Технически закрытых заказов за период нет.</p>}
      {data.items.map(o=><details key={o.workOrderId}>
        <summary>Заказ <code style={{wordBreak:'break-all'}}>{o.workOrderId}</code>
          · {o.closedDay} · {o.status==='COMPLETE'?'Источники подтверждены':
            'Неполные первичные затраты'}
          · условная разница: {format(o.syntheticDifferenceMinor)}</summary>
        <p>Согласовано {format(o.approvedTotalMinor)}.
          Условно внесено {format(o.simulatedPaidMinor)}, возвращено
          {format(o.simulatedRefundedMinor)}. Материалы {format(o.materialNetMinor)},
          труд {format(o.laborCostMinor)}. QC/документы и неизменяемое
          техническое закрытие: <code style={{wordBreak:'break-all'}}>{o.closedEventId}</code>.
          Версия сметы <code style={{wordBreak:'break-all'}}>{o.estimateRevisionId}</code>.</p>
        <details><summary>Источники условных платежей: {o.cashEvents.length}</summary>
          <ul>{o.cashEvents.map(e=><li key={e.id} style={{overflowWrap:'anywhere'}}>
            <code style={{wordBreak:'break-all'}}>{e.id}</code> · {e.kind} ·
            {format(e.amountMinor)} (только SIM)</li>)}</ul>
        </details>
        <details><summary>Доказательства труда: {o.laborEntries.length}</summary>
          <ul>{o.laborEntries.map(e=><li key={e.laborEntryId}
              style={{overflowWrap:'anywhere'}}>
            <code style={{wordBreak:'break-all'}}>{e.laborEntryId}</code> ·
            {e.actualMinutes} мин. · {e.status} · {format(e.costMinor)}
            {e.source&&<> · утверждение ставки
              <code style={{wordBreak:'break-all'}}>{e.source.approvalId}</code></>}
          </li>)}</ul>
        </details>
        <details><summary>Доказательства материалов: {o.materialGroups.length}</summary>
          <ul>{o.materialGroups.map((g,i)=><li key={g.skuId+':'+i}
              style={{overflowWrap:'anywhere'}}>
            SKU <code style={{wordBreak:'break-all'}}>{g.skuId}</code> ·
            {g.status} · {format(g.materialNetMinor)}
            {g.source&&<> · оценка
              <code style={{wordBreak:'break-all'}}>{g.source.valuationId}</code></>}
            <ul>{g.movements.map(m=><li key={m.stockEventId}>
              {m.action}: {m.quantity} · событие
              <code style={{wordBreak:'break-all'}}>{m.stockEventId}</code></li>)}</ul>
          </li>)}</ul>
        </details>
      </details>)}
      <button type="button" disabled={exporting} onClick={()=>void exportCsv()}>
        Скачать сверку с UUID первички (CSV)</button>
      <p>Экспорт ещё раз проверяет актуальную сессию, права на
        цены продажи, закупки и документы и все события в PostgreSQL.
        При 403/409/422 старая сводка немедленно скрывается.</p>
    </section>}
  </section>;
}
