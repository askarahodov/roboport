import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const amount=/^(0|[1-9]\d{0,17})$/;
const code=/^[A-Z0-9_-]{2,32}$/;
const today=()=>new Date().toISOString().slice(0,10);
type Flow='IN'|'OUT'|'INTERNAL';
type EventKind='ADVANCE'|'PAYMENT'|'REFUND_ADVANCE'|'REFUND_PAYMENT'|'APPLY_ADVANCE';
type Entry={eventId:string;kind:EventKind;amountMinor:string;
  cashFlow:Flow;createdAt:string};
type AccountDay={localDay:string;openingMinor:string;incomingMinor:string;
  outgoingMinor:string;internalAppliedMinor:string;closingMinor:string;
  closeId:string|null;events:Entry[]};
type Account={
  accountId:string;accountCode:string;accountKind:'CASH_SIM'|'BANK_SIM';
  summary:{openingMinor:string;incomingMinor:string;outgoingMinor:string;
    internalAppliedMinor:string;closingMinor:string;eventCount:number;sealedDayCount:number};
  days:AccountDay[];
};
type Day={localDay:string;openingMinor:string;incomingMinor:string;
  outgoingMinor:string;internalAppliedMinor:string;closingMinor:string;
  eventCount:number;sealedAccountCount:number};
type Report={
  syntheticOnly:true;realMoney:false;releaseAllowed:false;currency:'RUB';
  basis:'VERIFIED_BRANCH_CASHFLOW_FROM_IMMUTABLE_ACCOUNT_CASHBOOKS';
  fromDay:string;toDay:string;maxAccounts:50;maxEvents:1000;
  summary:{accountCount:number;eventCount:number;sealedAccountDayCount:number;
    openingMinor:string;incomingMinor:string;outgoingMinor:string;
    internalAppliedMinor:string;closingMinor:string};
  days:Day[];accounts:Account[];
};
const cents=(x:string)=>(BigInt(x)/100n).toLocaleString('ru-RU')+','+
  (BigInt(x)%100n).toString().padStart(2,'0');
function unit(x:unknown,max:bigint):bigint{
  if(typeof x!=='string'||!amount.test(x)||BigInt(x)>max)throw Error('amount');
  return BigInt(x);
}
const sum=(xs:bigint[])=>xs.reduce((a,b)=>a+b,0n);
const branchMax=50000000000000000n;
const accountMax=1000000000000000n;
const numbers=['openingMinor','incomingMinor','outgoingMinor',
  'internalAppliedMinor','closingMinor'] as const;
const validDay=(s:unknown)=>typeof s==='string'&&day.test(s)&&
  Number.isFinite(Date.parse(s+'T00:00:00Z'))&&
  new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const r=raw as Report;
  const length=(Date.parse(to+'T00:00:00Z')-
    Date.parse(from+'T00:00:00Z'))/86400000+1;
  if(r.syntheticOnly!==true||r.realMoney!==false||r.releaseAllowed!==false||
    r.currency!=='RUB'||r.basis!=='VERIFIED_BRANCH_CASHFLOW_FROM_IMMUTABLE_ACCOUNT_CASHBOOKS'||
    r.fromDay!==from||r.toDay!==to||r.maxAccounts!==50||r.maxEvents!==1000||
    !r.summary||!Array.isArray(r.days)||r.days.length!==length||
    !Array.isArray(r.accounts)||r.accounts.length>50||
    r.summary.accountCount!==r.accounts.length||
    !Number.isSafeInteger(r.summary.eventCount)||r.summary.eventCount<0||
    r.summary.eventCount>1000||!Number.isSafeInteger(r.summary.sealedAccountDayCount)||
    r.summary.sealedAccountDayCount<0)throw Error('shape');
  const ids=new Set<string>(),events=new Set<string>();
  const totals=Array.from({length},(_,i)=>({
    openingMinor:0n,incomingMinor:0n,outgoingMinor:0n,
    internalAppliedMinor:0n,closingMinor:0n,
    eventCount:0,sealedAccountCount:0,localDay:
      new Date(Date.parse(from+'T00:00:00Z')+i*86400000).toISOString().slice(0,10),
  }));
  for(const a of r.accounts){
    if(!a||!uuid.test(a.accountId)||ids.has(a.accountId)||
      !code.test(a.accountCode)||!['CASH_SIM','BANK_SIM'].includes(a.accountKind)||
      !a.summary||!Array.isArray(a.days)||a.days.length!==length||
      !Number.isSafeInteger(a.summary.eventCount)||a.summary.eventCount<0||
      !Number.isSafeInteger(a.summary.sealedDayCount)||a.summary.sealedDayCount<0)
      throw Error('account');
    ids.add(a.accountId);
    let previous:bigint|null=null;
    let incoming=0n,outgoing=0n,internal=0n,count=0,sealed=0;
    for(const [i,d] of a.days.entries()){
      if(!d||d.localDay!==totals[i].localDay||!validDay(d.localDay)||
        !Array.isArray(d.events)||d.events.length>1000||
        (d.closeId!==null&&!uuid.test(d.closeId)))throw Error('day');
      const amounts=numbers.map(k=>unit(d[k],accountMax));
      const [opening,inc,out,applied,closing]=amounts;
      if(opening+inc-out!==closing||
        (previous!==null&&opening!==previous))throw Error('balance');
      if(previous===null&&opening!==unit(a.summary.openingMinor,accountMax))
        throw Error('opening');
      previous=closing;
      if(d.closeId!==null)sealed++;
      const byFlow={IN:0n,OUT:0n,INTERNAL:0n};
      for(const e of d.events){
        if(!e||!uuid.test(e.eventId)||events.has(e.eventId)||
          !['ADVANCE','PAYMENT','REFUND_ADVANCE','REFUND_PAYMENT','APPLY_ADVANCE'].includes(e.kind)||
          !['IN','OUT','INTERNAL'].includes(e.cashFlow)||
          typeof e.createdAt!=='string'||!Number.isFinite(Date.parse(e.createdAt)))
          throw Error('source');
        const expectedFlow=['ADVANCE','PAYMENT'].includes(e.kind)?'IN':
          e.kind==='APPLY_ADVANCE'?'INTERNAL':'OUT';
        if(e.cashFlow!==expectedFlow)throw Error('flow');
        const v=unit(e.amountMinor,1000000000000n);
        if(v===0n)throw Error('zero source');
        byFlow[e.cashFlow]+=v;
        events.add(e.eventId);count++;
      }
      if(byFlow.IN!==inc||byFlow.OUT!==out||byFlow.INTERNAL!==applied)
        throw Error('source totals');
      incoming+=inc;outgoing+=out;internal+=applied;
      for(const k of numbers)totals[i][k]+=unit(d[k],accountMax);
      totals[i].eventCount+=d.events.length;
      if(d.closeId)totals[i].sealedAccountCount++;
    }
    if(count!==a.summary.eventCount||sealed!==a.summary.sealedDayCount||
      incoming!==unit(a.summary.incomingMinor,accountMax)||
      outgoing!==unit(a.summary.outgoingMinor,accountMax)||
      internal!==unit(a.summary.internalAppliedMinor,accountMax)||
      previous!==unit(a.summary.closingMinor,accountMax))throw Error('account summary');
  }
  if(events.size!==r.summary.eventCount||
    totals.reduce((n,d)=>n+d.sealedAccountCount,0)!==r.summary.sealedAccountDayCount)
    throw Error('summary');
  let previous:bigint|null=null;
  for(const [i,d] of r.days.entries()){
    if(!d||d.localDay!==totals[i].localDay||
      !Number.isSafeInteger(d.eventCount)||!Number.isSafeInteger(d.sealedAccountCount)||
      d.eventCount!==totals[i].eventCount||
      d.sealedAccountCount!==totals[i].sealedAccountCount)throw Error('branch day');
    for(const k of numbers)if(unit(d[k],branchMax)!==totals[i][k])throw Error('branch amount');
    const opening=unit(d.openingMinor,branchMax),closing=unit(d.closingMinor,branchMax);
    if(previous!==null&&opening!==previous)throw Error('branch continuity');
    previous=closing;
  }
  const first=r.days[0],last=r.days[r.days.length-1];
  if(!first||!last||
    unit(r.summary.openingMinor,branchMax)!==unit(first.openingMinor,branchMax)||
    unit(r.summary.closingMinor,branchMax)!==unit(last.closingMinor,branchMax)||
    unit(r.summary.incomingMinor,branchMax)!==
      sum(r.days.map(d=>unit(d.incomingMinor,branchMax)))||
    unit(r.summary.outgoingMinor,branchMax)!==
      sum(r.days.map(d=>unit(d.outgoingMinor,branchMax)))||
    unit(r.summary.internalAppliedMinor,branchMax)!==
      sum(r.days.map(d=>unit(d.internalAppliedMinor,branchMax))))
    throw Error('branch summary');
  return r;
}

const kindLabel:Record<EventKind,string>={
  ADVANCE:'Аванс',PAYMENT:'Условная оплата',
  REFUND_ADVANCE:'Возврат аванса',REFUND_PAYMENT:'Возврат оплаты',
  APPLY_ADVANCE:'Зачёт аванса без движения',
};

export function AutoBranchCashflow(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today),[to,setTo]=React.useState(today);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'limit'|'error'>('idle');
  const [report,setReport]=React.useState<Report|null>(null);
  const [busy,setBusy]=React.useState(false),[exportError,setExportError]=React.useState('');
  const request=React.useRef<AbortController|null>(null);
  const exportRequest=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{request.current?.abort();exportRequest.current?.abort();},[]);
  function clear(){
    request.current?.abort();exportRequest.current?.abort();
    setReport(null);setStatus('idle');setBusy(false);setExportError('');
  }
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&validDay(from)&&validDay(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  const url=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/treasury/cashflow?'+
    new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    request.current?.abort();exportRequest.current?.abort();
    const control=new AbortController();request.current=control;
    setReport(null);setStatus('loading');setExportError('');
    try{
      const response=await fetch(url(),{credentials:'include',cache:'no-store',
        signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(response.status===409){setStatus('limit');return;}
      if(!response.ok)throw Error('http');
      const data=parse(await response.json(),from,to);
      if(!control.signal.aborted){setReport(data);setStatus('ready');}
    }catch{if(!control.signal.aborted)setStatus('error');}
  }
  async function exportCsv(){
    if(!report||status!=='ready'||busy)return;
    exportRequest.current?.abort();
    const control=new AbortController();exportRequest.current=control;
    setBusy(true);setExportError('');
    try{
      const response=await fetch(url().replace('/cashflow?','/cashflow/export?'),{
        credentials:'include',cache:'no-store',signal:control.signal,
      });
      if(control.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setReport(null);setStatus('denied');return;
      }
      if(response.status===409){setReport(null);setStatus('limit');return;}
      if(response.status===422){setReport(null);setStatus('invalid');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('export');
      const blob=await response.blob();
      if(control.signal.aborted||blob.size>1024*1024||blob.size===0)throw Error('size');
      const ref=URL.createObjectURL(blob);
      const a=document.createElement('a');
      a.href=ref;a.download='auto-branch-cashflow.csv';
      document.body.appendChild(a);a.click();a.remove();
      window.setTimeout(()=>URL.revokeObjectURL(ref),1000);
    }catch{
      if(!control.signal.aborted){
        setReport(null);setStatus('error');
        setExportError('Экспорт недоступен. Обновите сверку и полномочия.');
      }
    }finally{if(exportRequest.current===control)setBusy(false);}
  }
  return <section aria-label="ДДС филиала по первичным событиям (dev)"
      style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}>
    <h1>ДДС филиала: проверяемая синтетическая сверка</h1>
    <p><strong>Только synthetic dev, не реальные деньги, не бухгалтерский
      ДДС и не кассовый чек.</strong> Объединяются отдельные тестовые кассы
      и счета филиала по неизменяемым событиям. Аванс и условная оплата —
      приток; возврат — отток; зачёт аванса не создаёт повторного притока.
      Остаток — не выручка и не прибыль. Для каждой суммы доступна первичка
      по UUID без ФИО, VIN, заказа и текстовых причин.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{clear();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{clear();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода"
      value={from} onChange={e=>{clear();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода"
      value={to} onChange={e=>{clear();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Сверить ДДС филиала</button>
    {!valid&&<p>Укажите UUID организации, филиала и период 1–31 день.</p>}
    {status==='loading'&&<p role="status">Сверяем источники в PostgreSQL…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или отозван. Данные очищены.</p>}
    {status==='invalid'&&<p role="alert">Неверный период. Допустимо 1–31 день.</p>}
    {status==='limit'&&<p role="alert">Конфликт или превышен лимит 50 счетов / 1000 событий. Сократите период.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить первичные источники ДДС.</p>}
    {report&&status==='ready'&&<section aria-label="Результаты ДДС филиала"
        style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}>
      <p role="status">Тестовых счетов: {report.summary.accountCount};
        событий: {report.summary.eventCount};
        начало: {cents(report.summary.openingMinor)} RUB;
        приход: {cents(report.summary.incomingMinor)} RUB;
        расход: {cents(report.summary.outgoingMinor)} RUB;
        зачтено без движения: {cents(report.summary.internalAppliedMinor)} RUB;
        конец: {cents(report.summary.closingMinor)} RUB.</p>
      <div role="region" tabIndex={0} aria-label="Прокручиваемая сводка ДДС"
          style={{overflowX:'auto',maxWidth:'100%',minWidth:0}}>
        <table aria-label="Движение по дням филиала">
          <thead><tr><th>Локальный день</th><th>Начало</th><th>Приход</th>
            <th>Расход</th><th>Зачёт без движения</th><th>Конец</th>
            <th>События</th><th>Закрытые счета</th></tr></thead>
          <tbody>{report.days.map(d=><tr key={d.localDay}>
            <td>{d.localDay}</td><td>{cents(d.openingMinor)}</td>
            <td>{cents(d.incomingMinor)}</td><td>{cents(d.outgoingMinor)}</td>
            <td>{cents(d.internalAppliedMinor)}</td>
            <td>{cents(d.closingMinor)}</td><td>{d.eventCount}</td>
            <td>{d.sealedAccountCount}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {report.accounts.length===0&&<p>В филиале ещё нет тестовых счетов.</p>}
      {report.accounts.map(a=><details key={a.accountId} style={{overflowWrap:'anywhere'}}>
        <summary>{a.accountCode} ({a.accountKind}) ·
          закрывающий остаток {cents(a.summary.closingMinor)} RUB ·
          источников: {a.summary.eventCount}</summary>
        <p>Счёт UUID: <code style={{wordBreak:'break-all'}}>{a.accountId}</code>.
          В исходной кассовой книге этого счёта доступна та же сверка.
          Все суммы тестовые.</p>
        {a.days.map(d=><details key={d.localDay} style={{overflowWrap:'anywhere'}}>
          <summary>{d.localDay}: приход {cents(d.incomingMinor)},
            расход {cents(d.outgoingMinor)},
            зачёт {cents(d.internalAppliedMinor)}.
            Событий: {d.events.length}.
            {d.closeId?' День закрыт.':' День не закрыт.'}</summary>
          <ul>{d.events.map(e=><li key={e.eventId}
              style={{minWidth:0,overflowWrap:'anywhere'}}>
            <code style={{wordBreak:'break-all'}}>{e.eventId}</code> —
            {kindLabel[e.kind]}: {cents(e.amountMinor)} RUB.
            Поток: {e.cashFlow}; запись {e.createdAt}.
          </li>)}</ul>
          {d.closeId&&<p>Событие закрытия:
            <code style={{wordBreak:'break-all'}}>{d.closeId}</code></p>}
        </details>)}
      </details>)}
      <button type="button" onClick={()=>void exportCsv()} disabled={busy}>
        Скачать полный ДДС с UUID первички (CSV)</button>
      {exportError&&<p role="alert">{exportError}</p>}
      <p>Повторный экспорт всегда проверяет актуальную роль
        MANAGER/CASHIER и FINANCE-политику. Доступ с ограничением
        исторических данных запрещён целиком; отказ стирает сводку.</p>
    </section>}
  </section>;
}
