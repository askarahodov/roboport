import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const digest=/^[0-9a-f]{64}$/;
const day=/^\d{4}-\d{2}-\d{2}$/;
const decimal=/^-?(0|[1-9]\d{0,18})$/;
const today=()=>new Date().toISOString().slice(0,10);
const basis='CLOSED_TECHNICAL_TWO_SEPARATE_SIMULATED_SETTLEMENT_LEDGERS';
type Kind='ADVANCE'|'PAYMENT'|'APPLY_ADVANCE'|'REFUND_ADVANCE'|'REFUND_PAYMENT';
type Status='NO_TREASURY_EVENTS'|'PARTIAL'|'SETTLED';
type LedgerEvent={eventId:string;accountId:string;revisionId:string;kind:Kind;
  amountMinor:string;businessDay:string};
type FifoSlice={stockEventId:string;lotMovementId:string;originalIssueId:string;
  receiptId:string;acceptanceId:string;valuationId:string;
  action:'ISSUE'|'RETURN';quantity:number;unitNetMinor:number;
  serialId:string|null;serialApprovalId:string|null;sourceLocationId:string|null};
type CostEvidence={labor:{laborEntryId:string;status:string;approvalId:string|null;
  rateRevisionId:string|null}[];lots:{lotId:string|null;skuId:string;status:string;
  receiptId:string|null;valuationId:string|null;fifoSlices:FifoSlice[]}[]};
type Order={workOrderId:string;closedDay:string;closedEventId:string;previewId:string;
  previewDigest:string;revisionId:string;approvedTotalMinor:string;
  directCostMinor:string|null;syntheticDifferenceMinor:string|null;
  costStatus:'COMPLETE'|'INCOMPLETE';demoNetMinor:string;ledgerStatus:Status;
  treasurySettledMinor:string;unappliedAdvanceMinor:string;treasuryCashNetMinor:string;
  treasuryDueMinor:string;demoMinusTreasuryMinor:string;
  treasuryEvents:LedgerEvent[];costEvidence:CostEvidence};
type Summary={orderCount:number;costCompleteCount:number;ledgerLinkedCount:number;
  ledgerSettledCount:number;treasuryEventCount:number;
  approvedTotalMinor:string;demoNetMinor:string;treasurySettledMinor:string;
  unappliedAdvanceMinor:string;treasuryCashNetMinor:string;treasuryDueMinor:string;
  demoMinusTreasuryMinor:string};
type Report={syntheticOnly:true;realMoney:false;releaseAllowed:false;
  marginAvailable:false;payrollAvailable:false;currency:'RUB';basis:typeof basis;
  timeZone:string;fromDay:string;toDay:string;summary:Summary;items:Order[]};

const fields=['approvedTotalMinor','demoNetMinor','treasurySettledMinor',
  'unappliedAdvanceMinor','treasuryCashNetMinor','treasuryDueMinor',
  'demoMinusTreasuryMinor'] as const;
const keys=['ADVANCE','PAYMENT','APPLY_ADVANCE','REFUND_ADVANCE','REFUND_PAYMENT'] as const;
function money(n:unknown,signed=false):bigint{
  if(typeof n!=='string'||!decimal.test(n))throw Error('amount');
  const v=BigInt(n),bound=1801439850948198200n;
  if((!signed&&v<0n)||v>bound||v< -bound)throw Error('bounded amount');
  return v;
}
const sum=(v:bigint[])=>v.reduce((a,b)=>a+b,0n);
const cash=(n:string|null)=>{
  if(n===null)return 'Нет доказанной стоимости';
  const v=BigInt(n),a=v<0n?-v:v;
  return (v<0n?'-':'')+(a/100n).toLocaleString('ru-RU')+','+
    (a%100n).toString().padStart(2,'0')+' RUB';
};
const isUuid=(v:unknown):v is string=>typeof v==='string'&&uuid.test(v);
const isDay=(s:unknown)=>typeof s==='string'&&day.test(s)&&
  Number.isFinite(Date.parse(s+'T00:00:00Z'))&&
  new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
const statusLabel:Record<Status,string>={
  NO_TREASURY_EVENTS:'Нет записей в отдельной тестовой кассе',
  PARTIAL:'Частичный тестовый расчёт или аванс',
  SETTLED:'Техническая сумма зачтена в тестовой кассе',
};
const kindLabel:Record<Kind,string>={
  ADVANCE:'Аванс',PAYMENT:'Тестовая оплата',
  APPLY_ADVANCE:'Зачёт аванса (не приход)',
  REFUND_ADVANCE:'Возврат аванса',REFUND_PAYMENT:'Возврат оплаты',
};

function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('response');
  const r=raw as Report;
  if(r.syntheticOnly!==true||r.realMoney!==false||r.releaseAllowed!==false||
    r.marginAvailable!==false||r.payrollAvailable!==false||r.currency!=='RUB'||
    r.basis!==basis||r.fromDay!==from||r.toDay!==to||!r.timeZone||
    !r.summary||!Array.isArray(r.items)||r.items.length>200||
    r.summary.orderCount!==r.items.length)throw Error('report');
  const totals=Object.fromEntries(fields.map(k=>[k,0n])) as Record<typeof fields[number],bigint>;
  let costComplete=0,linked=0,settledCount=0,count=0;
  const seenOrders=new Set<string>(),seenEvents=new Set<string>();
  for(const x of r.items){
    if(!x||!isUuid(x.workOrderId)||seenOrders.has(x.workOrderId)||
      !isUuid(x.closedEventId)||!isUuid(x.previewId)||!digest.test(x.previewDigest)||
      !isUuid(x.revisionId)||!isDay(x.closedDay)||
      !['COMPLETE','INCOMPLETE'].includes(x.costStatus)||
      !['NO_TREASURY_EVENTS','PARTIAL','SETTLED'].includes(x.ledgerStatus)||
      !Array.isArray(x.treasuryEvents)||!x.costEvidence||
      !Array.isArray(x.costEvidence.labor)||!Array.isArray(x.costEvidence.lots))
      throw Error('order');
    seenOrders.add(x.workOrderId);
    const approved=money(x.approvedTotalMinor),demo=money(x.demoNetMinor);
    const receipt=money(x.treasurySettledMinor),advance=money(x.unappliedAdvanceMinor),
      net=money(x.treasuryCashNetMinor),due=money(x.treasuryDueMinor),
      difference=money(x.demoMinusTreasuryMinor);
    if(demo!==approved||net!==receipt+advance||due!==approved-receipt||
      difference!==demo-receipt||receipt+advance>approved)throw Error('ledger totals');
    const direct=x.directCostMinor===null?null:money(x.directCostMinor);
    const margin=x.syntheticDifferenceMinor===null?null:money(x.syntheticDifferenceMinor,true);
    if((x.costStatus==='COMPLETE')!==(direct!==null&&margin!==null)||
      (direct!==null&&margin!==approved-direct))throw Error('cost totals');
    if(x.costStatus==='COMPLETE')costComplete++;
    if(x.ledgerStatus!=='NO_TREASURY_EVENTS')linked++;
    if(x.ledgerStatus==='SETTLED')settledCount++;
    const amounts=Object.fromEntries(keys.map(k=>[k,0n])) as Record<Kind,bigint>;
    for(const e of x.treasuryEvents){
      if(!e||!isUuid(e.eventId)||seenEvents.has(e.eventId)||
        !isUuid(e.accountId)||e.revisionId!==x.revisionId||
        !keys.includes(e.kind)||!isDay(e.businessDay))throw Error('event');
      const amount=money(e.amountMinor);
      if(amount<1n||amount>1000000000000n)throw Error('source amount');
      seenEvents.add(e.eventId);
      amounts[e.kind]+=amount;
      if(++count>500)throw Error('source bound');
    }
    const accrued=amounts.PAYMENT+amounts.APPLY_ADVANCE-amounts.REFUND_PAYMENT;
    const unspent=amounts.ADVANCE-amounts.APPLY_ADVANCE-amounts.REFUND_ADVANCE;
    const cashNet=amounts.ADVANCE+amounts.PAYMENT-
      amounts.REFUND_ADVANCE-amounts.REFUND_PAYMENT;
    const status=x.treasuryEvents.length===0?'NO_TREASURY_EVENTS':
      accrued===approved&&unspent===0n?'SETTLED':'PARTIAL';
    if(accrued<0n||unspent<0n||cashNet<0n||
      accrued!==receipt||unspent!==advance||cashNet!==net||
      status!==x.ledgerStatus)throw Error('reconciliation');
    for(const key of fields)totals[key]+=money(x[key]);
    if(x.costEvidence.labor.length>500||x.costEvidence.lots.length>500)throw Error('evidence');
    for(const item of x.costEvidence.labor){
      if(!isUuid(item.laborEntryId)||
        (item.approvalId!==null&&!isUuid(item.approvalId))||
        (item.rateRevisionId!==null&&!isUuid(item.rateRevisionId)))throw Error('labor');
    }
    for(const item of x.costEvidence.lots){
      if(!isUuid(item.skuId)||(item.lotId!==null&&!isUuid(item.lotId))||
        (item.receiptId!==null&&!isUuid(item.receiptId))||
        (item.valuationId!==null&&!isUuid(item.valuationId))||
        !Array.isArray(item.fifoSlices)||item.fifoSlices.length>5000)
        throw Error('material');
      if(['FIFO_TRACED','SERIAL_TRACED'].includes(item.status)&&
         (!item.fifoSlices.length||item.receiptId!==null||item.valuationId!==null))
        throw Error('missing fifo provenance');
      for(const slice of item.fifoSlices){
        if(!slice||![slice.stockEventId,slice.lotMovementId,
            slice.originalIssueId,slice.receiptId,slice.acceptanceId,
            slice.valuationId].every(isUuid)||
          !['ISSUE','RETURN'].includes(slice.action)||
          !Number.isSafeInteger(slice.quantity)||slice.quantity<1||
          slice.quantity>1e9||!Number.isSafeInteger(slice.unitNetMinor)||
          slice.unitNetMinor<0||slice.unitNetMinor>1e9||
          (slice.serialId!==null&&!isUuid(slice.serialId))||
          (slice.serialApprovalId!==null&&!isUuid(slice.serialApprovalId))||
          (slice.sourceLocationId!==null&&!isUuid(slice.sourceLocationId))||
          (item.status==='SERIAL_TRACED'&&
            (!isUuid(slice.serialId)||!isUuid(slice.serialApprovalId))))
          throw Error('invalid slice');
      }
    }
  }
  if(r.summary.costCompleteCount!==costComplete||
    r.summary.ledgerLinkedCount!==linked||r.summary.ledgerSettledCount!==settledCount||
    r.summary.treasuryEventCount!==count)throw Error('counts');
  for(const k of fields)if(money(r.summary[k])!==totals[k])throw Error('summary');
  return r;
}

export function AutoClosedOrderLedgerReconciliation(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today),[to,setTo]=React.useState(today);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'limit'|'error'>('idle');
  const [busy,setBusy]=React.useState(false);
  const req=React.useRef<AbortController|null>(null);
  const exportReq=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{req.current?.abort();exportReq.current?.abort();},[]);
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=isUuid(org)&&isUuid(loc)&&isDay(from)&&isDay(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  function clear(){
    req.current?.abort();exportReq.current?.abort();
    setReport(null);setStatus('idle');setBusy(false);
  }
  const url=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+
    '/reports/closed-order-ledger-reconciliation?'+
    new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    req.current?.abort();exportReq.current?.abort();
    const controller=new AbortController();req.current=controller;
    setReport(null);setStatus('loading');setBusy(false);
    try{
      const response=await fetch(url(),{credentials:'include',cache:'no-store',
        signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===409){setStatus('limit');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(!response.ok)throw Error('http');
      const data=parse(await response.json(),from,to);
      if(!controller.signal.aborted){setReport(data);setStatus('ready');}
    }catch{if(!controller.signal.aborted)setStatus('error');}
  }
  async function download(){
    if(!report||status!=='ready'||busy)return;
    exportReq.current?.abort();
    const controller=new AbortController();exportReq.current=controller;
    setBusy(true);
    try{
      const response=await fetch(url().replace('reconciliation?','reconciliation/export?'),{
        credentials:'include',cache:'no-store',signal:controller.signal,
      });
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setReport(null);setStatus('denied');return;
      }
      if(response.status===409){setReport(null);setStatus('limit');return;}
      if(response.status===422){setReport(null);setStatus('invalid');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('export');
      const data=await response.blob();
      if(controller.signal.aborted||!data.size||data.size>1024*1024)
        throw Error('size');
      const blob=URL.createObjectURL(data);
      const anchor=document.createElement('a');
      anchor.href=blob;anchor.download='auto-closed-ledger-reconciliation.csv';
      document.body.appendChild(anchor);anchor.click();anchor.remove();
      window.setTimeout(()=>URL.revokeObjectURL(blob),1000);
    }catch{if(!controller.signal.aborted){setReport(null);setStatus('error');}}
    finally{if(exportReq.current===controller)setBusy(false);}
  }
  return <section aria-label="Сверка закрытых ЗН с двумя реестрами (dev)"
      style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}>
    <h1>Закрытые ЗН: первичка, условные расчёты и прямые затраты</h1>
    <p><strong>Только синтетический dev: ни реальных платежей, ни прибыли,
      зарплаты, бухгалтерского закрытия или выдачи автомобиля.</strong>
      Имитации оплат в пакете CLOSED_TECHNICAL и события тестовой кассы —
      <strong> два независимых журнала</strong>. Даже если суммы совпадают,
      это не подтверждает банковское поступление. Вычитание модельных
      прямых затрат из согласованной сметы — условная разница, не маржа.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{clear();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{clear();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода"
      value={from} onChange={e=>{clear();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода"
      value={to} onChange={e=>{clear();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Сверить закрытые ЗН и два реестра</button>
    {!valid&&<p>Укажите UUID своей организации/филиала и даты 1–31 день.</p>}
    {status==='loading'&&<p role="status">Проверяем закрытые документы и журналы…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или отозван. Старые сведения скрыты.</p>}
    {status==='invalid'&&<p role="alert">Некорректная дата или период длиннее 31 дня.</p>}
    {status==='limit'&&<p role="alert">Конфликт источников или превышен лимит: повторите позже либо сократите период.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить полную первичку закрытых заказов.</p>}
    {report&&status==='ready'&&<section aria-label="Сверка закрытых заказов"
        style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}>
      <p role="status">Закрытых ЗН: {report.summary.orderCount};
        доказанные затраты: {report.summary.costCompleteCount};
        с кассовыми событиями: {report.summary.ledgerLinkedCount};
        условно оплачено полностью: {report.summary.ledgerSettledCount};
        исходных событий: {report.summary.treasuryEventCount}.
        Условная смета: {cash(report.summary.approvedTotalMinor)};
        зачтено отдельной кассой: {cash(report.summary.treasurySettledMinor)};
        остаток незачтённого аванса: {cash(report.summary.unappliedAdvanceMinor)};
        долг по кассовому журналу: {cash(report.summary.treasuryDueMinor)}.</p>
      <div role="region" tabIndex={0} aria-label="Таблица сравнения реестров"
          style={{width:'100%',maxWidth:'100%',minWidth:0,overflowX:'auto'}}>
        <table aria-label="Закрытые ЗН и два журнала">
          <thead><tr><th>Заказ</th><th>День закрытия</th>
            <th>Условная смета</th><th>Прямые затраты</th>
            <th>Условная разница</th><th>Имитация расчёта</th>
            <th>Отдельная касса</th><th>Незачтённый аванс</th>
            <th>Тестовый долг</th><th>Статус кассы</th></tr></thead>
          <tbody>{report.items.map(x=><tr key={x.workOrderId}>
            <td><code style={{wordBreak:'break-all'}}>{x.workOrderId}</code></td>
            <td>{x.closedDay}</td><td>{cash(x.approvedTotalMinor)}</td>
            <td>{cash(x.directCostMinor)}</td><td>{cash(x.syntheticDifferenceMinor)}</td>
            <td>{cash(x.demoNetMinor)}</td><td>{cash(x.treasurySettledMinor)}</td>
            <td>{cash(x.unappliedAdvanceMinor)}</td><td>{cash(x.treasuryDueMinor)}</td>
            <td>{statusLabel[x.ledgerStatus]}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {report.items.length===0&&<p>Нет технически закрытых ЗН за этот период.</p>}
      {report.items.map(x=><details key={x.workOrderId}>
        <summary style={{overflowWrap:'anywhere'}}>Первичные UUID заказ-наряда
          <code style={{wordBreak:'break-all'}}>{x.workOrderId}</code> ·
          {statusLabel[x.ledgerStatus]} · {x.costStatus==='COMPLETE'?'затраты доказаны':'затраты не полностью доказаны'}</summary>
        <p>Событие закрытия: <code style={{wordBreak:'break-all'}}>{x.closedEventId}</code>;
          версия сметы: <code style={{wordBreak:'break-all'}}>{x.revisionId}</code>;
          пакет: <code style={{wordBreak:'break-all'}}>{x.previewId}</code>;
          SHA-256: <code style={{wordBreak:'break-all'}}>{x.previewDigest}</code>.</p>
        <p>Разница между имитацией расчёта и отдельной тестовой кассой:
          {cash(x.demoMinusTreasuryMinor)}. Это **не** задолженность
          в настоящей бухгалтерии.</p>
        {x.treasuryEvents.length===0&&<p>В независимом кассовом журнале
          событий нет; техническое закрытие не означает получение денег.</p>}
        <ul>{x.treasuryEvents.map(e=><li key={e.eventId}
            style={{overflowWrap:'anywhere',minWidth:0}}>
          {kindLabel[e.kind]}: {cash(e.amountMinor)} · {e.businessDay}.
          Событие: <code style={{wordBreak:'break-all'}}>{e.eventId}</code>;
          счёт: <code style={{wordBreak:'break-all'}}>{e.accountId}</code>.
        </li>)}</ul>
        <details><summary>Доказательства материалов и труда</summary>
          <ul>{x.costEvidence.labor.map(e=><li key={e.laborEntryId}
              style={{overflowWrap:'anywhere'}}>
            Труд: <code style={{wordBreak:'break-all'}}>{e.laborEntryId}</code> ·
            {e.status}; ставка: {e.approvalId??'нет утверждения'}.
          </li>)}</ul>
          <ul>{x.costEvidence.lots.map((e,i)=><li key={e.skuId+':'+i}
              style={{overflowWrap:'anywhere'}}>
            Материал: <code style={{wordBreak:'break-all'}}>{e.skuId}</code> ·
            {e.status}; партия: {e.lotId??'не доказана'},
            оценка: {e.valuationId??(
              e.fifoSlices.length?'по первичным FIFO/serial-срезам':'не доказана')}.
            {e.fifoSlices.length>0&&<ul aria-label="Доказательства распределения стоимости">
              {e.fifoSlices.map((part,j)=><li key={part.stockEventId+':'+j}
                  style={{overflowWrap:'anywhere',minWidth:0}}>
                {part.action==='ISSUE'?'Выдача':'Возврат'} {part.quantity} шт.
                × {cash(String(part.unitNetMinor))}.
                Приход: <code style={{wordBreak:'break-all'}}>{part.receiptId}</code>;
                приёмка: <code style={{wordBreak:'break-all'}}>{part.acceptanceId}</code>;
                оценка: <code style={{wordBreak:'break-all'}}>{part.valuationId}</code>;
                складское событие: <code style={{wordBreak:'break-all'}}>{part.stockEventId}</code>;
                движение партии: <code style={{wordBreak:'break-all'}}>{part.lotMovementId}</code>;
                исходная выдача: <code style={{wordBreak:'break-all'}}>{part.originalIssueId}</code>.
                {part.serialId&&<> Серийный номер UUID:
                  <code style={{wordBreak:'break-all'}}>{part.serialId}</code>;
                  утверждение происхождения:
                  <code style={{wordBreak:'break-all'}}>{part.serialApprovalId}</code>;
                  филиал-источник: {part.sourceLocationId??'отсутствует'}.</>}
              </li>)}
            </ul>}
          </li>)}</ul>
        </details>
      </details>)}
      <button type="button" disabled={busy} onClick={()=>void download()}>
        Скачать сверку с первичными UUID (CSV)</button>
      <p>CSV пересчитывается из PostgreSQL и проверяет права заново.
        Доступ MANAGER + FINANCE (включая цену закупки),
        ESTIMATE/HANDOVER и SUPPLIER_ORDER обязателен.
        Данные прежнего владельца и чужого филиала недоступны.</p>
    </section>}
  </section>;
}
