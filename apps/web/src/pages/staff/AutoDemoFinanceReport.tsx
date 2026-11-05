import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
type Item={
  localDay:string;chargeEvents:number;chargeMinor:number;
  creditEvents:number;creditMinor:number;correctionNetMinor:number;
  demoPaymentEvents:number;demoPaymentMinor:number;
  demoRefundEvents:number;demoRefundMinor:number;demoNetMinor:number;
  sealedPreviewCount:number;
};
type Report={
  syntheticOnly:true;legalGatePassed:false;releaseAllowed:false;
  financeCurrency:null;demoCurrency:'RUB';
  timeZone:string;basis:'INDEPENDENT_LEDGER_EVENT_LOCAL_DAYS';items:Item[];
};
const today=()=>new Date().toISOString().slice(0,10);
const isInt=(n:unknown):n is number=>Number.isSafeInteger(n)&&Number(n)>=0;
const money=(n:number)=>(n/100).toLocaleString('ru-RU',{
  minimumFractionDigits:2,maximumFractionDigits:2,
});
function validate(data:unknown):data is Report{
  if(!data||typeof data!=='object')return false;
  const r=data as Report;
  return r.syntheticOnly===true&&r.legalGatePassed===false&&
    r.releaseAllowed===false&&r.financeCurrency===null&&
    r.demoCurrency==='RUB'&&r.basis==='INDEPENDENT_LEDGER_EVENT_LOCAL_DAYS'&&
    typeof r.timeZone==='string'&&r.timeZone.length>0&&
    Array.isArray(r.items)&&r.items.length>=1&&r.items.length<=31&&
    r.items.every(row=>row&&typeof row.localDay==='string'&&day.test(row.localDay)&&
      [row.chargeEvents,row.chargeMinor,row.creditEvents,row.creditMinor,
        row.demoPaymentEvents,row.demoPaymentMinor,row.demoRefundEvents,
        row.demoRefundMinor,row.sealedPreviewCount].every(isInt)&&
      Number.isSafeInteger(row.correctionNetMinor)&&
      Number.isSafeInteger(row.demoNetMinor)&&
      row.correctionNetMinor===row.chargeMinor-row.creditMinor&&
      row.demoNetMinor===row.demoPaymentMinor-row.demoRefundMinor);
}

export function AutoDemoFinanceReport(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today);
  const [to,setTo]=React.useState(today);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'error'>('idle');
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  function reset(){active.current?.abort();active.current=null;setReport(null);setStatus('idle');}
  const days=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const allowed=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(days)&&days>=1&&days<=31;
  async function load(){
    if(!allowed)return;
    active.current?.abort();
    const controller=new AbortController();active.current=controller;
    setReport(null);setStatus('loading');
    try{
      const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
        '/locations/'+encodeURIComponent(loc)+'/demo-finance-report?'+
        new URLSearchParams({fromDay:from,toDay:to});
      const response=await fetch(url,{credentials:'include',cache:'no-store',
        signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(!response.ok)throw Error('HTTP');
      const payload:unknown=await response.json();
      if(!validate(payload)||payload.items.length!==days||
         payload.items[0]?.localDay!==from||
         payload.items[payload.items.length-1]?.localDay!==to)throw Error('CONTRACT');
      if(controller.signal.aborted)return;
      setReport(payload);setStatus('ready');
    }catch{
      if(!controller.signal.aborted)setStatus('error');
    }
  }
  return <section aria-label="Синтетические финансовые журналы (dev)">
    <h1>Сверка условных начислений и имитации расчётов</h1>
    <p><strong>Только закрытый synthetic-dev стенд.</strong> Два независимых
      журнала: условные CHARGE/CREDIT без валюты и тестовые SIM_PAYMENT/SIM_REFUND
      в условных копейках RUB. Суммы нельзя складывать между журналами.
      Это не фактическая выручка, денежный поток, прибыль, кассовый чек или платёж.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода"
      value={from} onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода"
      value={to} onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!allowed||status==='loading'} onClick={()=>void load()}>
      Сформировать сверку</button>
    {!allowed&&<p>Укажите UUID организации и филиала и период от 1 до 31 суток.</p>}
    {status==='loading'&&<p role="status">Сверка событий…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён. Требуется действующая роль руководителя филиала.</p>}
    {status==='invalid'&&<p role="alert">Период или идентификатор некорректны.</p>}
    {status==='error'&&<p role="alert">Не удалось получить сверку. Повторите запрос.</p>}
    {status==='ready'&&report&&<div>
      <p role="status">Отчёт сформирован · {report.timeZone}. Это суммы событий по
        локальным датам записи, не баланс заказов, не закрытие кассы и не реальные деньги.</p>
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Независимые условный и симулированный журналы по дням</caption>
          <thead><tr>
            <th scope="col">День</th>
            <th scope="col">Начисления, событий</th>
            <th scope="col">Начисления, без валюты</th>
            <th scope="col">Корректировки, событий</th>
            <th scope="col">Корректировки, без валюты</th>
            <th scope="col">Сальдо корректировок, без валюты</th>
            <th scope="col">Имитации оплат, событий</th>
            <th scope="col">Имитации оплат, RUB</th>
            <th scope="col">Имитации возвратов, событий</th>
            <th scope="col">Имитации возвратов, RUB</th>
            <th scope="col">Сальдо имитации, RUB</th>
            <th scope="col">Запечатано тестовых пакетов</th>
          </tr></thead>
          <tbody>{report.items.map(x=><tr key={x.localDay}>
            <th scope="row">{x.localDay}</th>
            <td>{x.chargeEvents}</td><td>{money(x.chargeMinor)}</td>
            <td>{x.creditEvents}</td><td>{money(x.creditMinor)}</td>
            <td>{money(x.correctionNetMinor)}</td>
            <td>{x.demoPaymentEvents}</td><td>{money(x.demoPaymentMinor)}</td>
            <td>{x.demoRefundEvents}</td><td>{money(x.demoRefundMinor)}</td>
            <td>{money(x.demoNetMinor)}</td><td>{x.sealedPreviewCount}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </div>}
  </section>;
}
