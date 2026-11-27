import React from 'react';

const fields=['orderCount','uniqueCustomers','singleOrderCustomers','multipleOrderCustomers',
  'additionalClosedOrders','approvedTotalMinor','simulatedPaidMinor','simulatedRefundedMinor'] as const;
type Field=typeof fields[number];
type Period=Record<Field,number>&{fromDay:string;toDay:string;multipleOrderBasisPoints:number|null;
  days:{day:string;closedOrders:number}[]};
type Report={syntheticOnly:true;realMoney:false;releaseAllowed:false;retentionAvailable:false;
  actualVisitsAvailable:false;basis:string;currency:'RUB';timeZone:string;current:Period;
  previous:Period;delta:Record<Field,number>};
const names:Record<Field,string>={orderCount:'Закрытые наряды',uniqueCustomers:'Уникальные клиенты',
  singleOrderCustomers:'Клиенты с одним нарядом',multipleOrderCustomers:'Клиенты с несколькими нарядами',
  additionalClosedOrders:'Дополнительные наряды',approvedTotalMinor:'Утверждено, RUB (условно)',
  simulatedPaidMinor:'Имитации оплат, RUB',simulatedRefundedMinor:'Имитации возвратов, RUB'};
const nonnegative=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0;
const dayMs=86400000;
const iso=(n:number)=>new Date(n).toISOString().slice(0,10);
function parse(value:unknown,from:string,to:string):Report{
  const r=value as Report;
  if(!r||r.syntheticOnly!==true||r.realMoney!==false||r.releaseAllowed!==false||
    r.retentionAvailable!==false||r.actualVisitsAvailable!==false||r.currency!=='RUB'||
    r.basis!=='RECONCILED_CLOSED_ORDER_PERIOD_COMPARISON'||typeof r.timeZone!=='string'||!r.timeZone||
    !r.current||!r.previous||!r.delta)throw Error('contract');
  const start=Date.parse(from),end=Date.parse(to),length=(end-start)/dayMs+1;
  if(!Number.isInteger(length)||length<1||length>31)throw Error('date');
  function period(p:Period,first:number,last:number){
    if(p.fromDay!==iso(first)||p.toDay!==iso(last)||!fields.every(k=>nonnegative(p[k]))||
      p.orderCount>200||p.uniqueCustomers!==p.singleOrderCustomers+p.multipleOrderCustomers||
      p.orderCount!==p.uniqueCustomers+p.additionalClosedOrders||
      p.additionalClosedOrders<p.multipleOrderCustomers||
      p.simulatedPaidMinor-p.simulatedRefundedMinor!==p.approvedTotalMinor||
      p.multipleOrderBasisPoints!==(p.uniqueCustomers?Math.floor(p.multipleOrderCustomers*10000/p.uniqueCustomers):null)||
      !Array.isArray(p.days)||p.days.length!==length||
      !p.days.every((d,i)=>d&&d.day===iso(first+i*dayMs)&&nonnegative(d.closedOrders)&&d.closedOrders<=200)||
      p.days.reduce((sum,d)=>sum+d.closedOrders,0)!==p.orderCount)throw Error('period');
  }
  period(r.current,start,end);period(r.previous,start-length*dayMs,start-dayMs);
  if(!fields.every(k=>Number.isSafeInteger(r.delta[k])&&r.delta[k]===r.current[k]-r.previous[k]))throw Error('delta');
  return r;
}
export function ClosedOrderComparison({org,loc,from,to,allowed}:{org:string;loc:string;from:string;to:string;allowed:boolean}){
  const [data,setData]=React.useState<Report|null>(null),[status,setStatus]=React.useState('idle');
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>{setData(null);setStatus('idle');return()=>active.current?.abort();},[org,loc,from,to,allowed]);
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/locations/'+encodeURIComponent(loc)+'/reports/closed-orders/comparison';
  async function load(exportFile=false){
    if(!allowed)return;
    active.current?.abort();const c=new AbortController();active.current=c;
    if(!exportFile)setData(null);
    setStatus('loading');
    try{
      const response=await fetch(base+(exportFile?'/export':'')+'?'+new URLSearchParams({fromDay:from,toDay:to}),
        {credentials:'include',cache:'no-store',signal:c.signal});
      if(c.signal.aborted)return;
      if([401,403,404].includes(response.status)){setData(null);setStatus('denied');return;}
      if(response.status===409){setData(null);setStatus('conflict');return;}
      if(!response.ok)throw Error('http');
      if(exportFile){
        if(!response.headers.get('content-type')?.startsWith('text/csv'))throw Error('csv');
        const digest=response.headers.get('X-Report-SHA256');
        if(!digest||!(/^[0-9a-f]{64}$/).test(digest))throw Error('digest');
        const bytes=await response.arrayBuffer();if(c.signal.aborted)return;
        if(bytes.byteLength>131072)throw Error('limit');
        const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
        if(actual!==digest)throw Error('digest');if(c.signal.aborted)return;
        const href=URL.createObjectURL(new Blob([bytes],{type:'text/csv'})),a=document.createElement('a');a.href=href;a.download='auto-closed-order-comparison.csv';
        document.body.appendChild(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(href),1000);
      }else{
        const parsed=parse(await response.json(),from,to);if(c.signal.aborted)return;setData(parsed);
      }
      setStatus('ready');
    }catch{if(!c.signal.aborted){setData(null);setStatus('error');}}
  }
  function format(key:Field,v:number){return key.endsWith('Minor')?(v/100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}):String(v);}
  return <section aria-label="Сравнение периодов закрытых нарядов">
    <h3>Сравнение периодов закрытых нарядов</h3>
    <p>Предыдущий период такой же длины, без пересечения. Только технически закрытые и сверенные наряды;
      несколько нарядов клиента за период не означают доказанный повторный визит или удержание.</p>
    <button type="button" disabled={!allowed||status==='loading'} onClick={()=>void load()}>Сравнить периоды</button>
    {status==='loading'&&<p role="status">Сверка периодов…</p>}
    {status==='denied'&&<p role="alert">Доступ к сравнению запрещён.</p>}
    {status==='conflict'&&<p role="alert">Сравнение не подтверждено. Уточните период или первичные документы.</p>}
    {status==='error'&&<p role="alert">Не удалось получить сравнение периодов.</p>}
    {status==='ready'&&data&&<>
      <p>Часовой пояс: {data.timeZone}. Текущий: {data.current.fromDay} — {data.current.toDay}.
        Предыдущий: {data.previous.fromDay} — {data.previous.toDay}.</p>
      <button type="button" onClick={()=>void load(true)}>Скачать сравнение CSV</button>
      <div style={{overflowX:'auto'}}><table><caption>Сравнение показателей</caption>
        <thead><tr><th scope="col">Показатель</th><th scope="col">Текущий</th><th scope="col">Предыдущий</th><th scope="col">Разница</th></tr></thead>
        <tbody>{fields.map(k=><tr key={k}><th scope="row">{names[k]}</th><td>{format(k,data.current[k])}</td>
          <td>{format(k,data.previous[k])}</td><td>{format(k,data.delta[k])}</td></tr>)}</tbody></table></div>
      <p>Доля клиентов с несколькими нарядами: {data.current.multipleOrderBasisPoints===null?'—':(data.current.multipleOrderBasisPoints/100).toLocaleString('ru-RU')+'%'}.</p>
      <div style={{overflowX:'auto'}}><table><caption>Закрытые наряды по дням</caption>
        <thead><tr><th scope="col">Текущий день</th><th scope="col">Наряды</th><th scope="col">Предыдущий день</th><th scope="col">Наряды</th></tr></thead>
        <tbody>{data.current.days.map((d,i)=><tr key={d.day}><td>{d.day}</td><td>{d.closedOrders}</td>
          <td>{data.previous.days[i].day}</td><td>{data.previous.days[i].closedOrders}</td></tr>)}</tbody></table></div>
    </>}
  </section>;
}
