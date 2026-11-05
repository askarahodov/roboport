import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const sources=['WEB','PHONE','WALK_IN'] as const;
const sourceNames:Record<string,string>={
  WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение',ALL:'Все источники',
};
type Reasons={PRICE:number;TIMING:number;NO_RESPONSE:number;OTHER:number};
type Group={sourceCode:string;total:number;new:number;qualified:number;
  booked:number;lost:number;bookedBasisPoints:number|null;lossReasons:Reasons};
type Report={syntheticOnly:true;basis:'LEAD_CREATED_COHORT_CURRENT_STAGE';
  timeZone:string;fromDay:string;toDay:string;summary:Group;items:Group[]};
const today=()=>new Date().toISOString().slice(0,10);
const reasons=['PRICE','TIMING','NO_RESPONSE','OTHER'] as const;
const reasonNames:Record<keyof Reasons,string>={
  PRICE:'Стоимость',TIMING:'Сроки',NO_RESPONSE:'Нет ответа',OTHER:'Прочее',
};
const fmt=(value:number)=>value.toLocaleString('ru-RU');
const percent=(value:number|null)=>value===null?'—':
  (value/100).toLocaleString('ru-RU',{minimumFractionDigits:2,
    maximumFractionDigits:2})+' %';

function groupValid(value:unknown,code:string):value is Group{
  if(!value||typeof value!=='object')return false;
  const v=value as Partial<Group>;
  if(v.sourceCode!==code||
    ![v.total,v.new,v.qualified,v.booked,v.lost].every(n=>
      typeof n==='number'&&Number.isSafeInteger(n)&&n>=0)||
    !v.lossReasons||
    !reasons.every(r=>typeof v.lossReasons?.[r]==='number'&&
      Number.isSafeInteger(v.lossReasons[r])&&v.lossReasons[r]>=0))return false;
  const row=v as Group;
  return row.total===row.new+row.qualified+row.booked+row.lost&&
    row.lost===reasons.reduce((sum,r)=>sum+row.lossReasons[r],0)&&
    row.bookedBasisPoints===(row.total?
      Math.floor(row.booked*10000/row.total):null);
}

function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as Partial<Report>;
  if(x.syntheticOnly!==true||x.basis!=='LEAD_CREATED_COHORT_CURRENT_STAGE'||
    !x.timeZone||typeof x.timeZone!=='string'||x.timeZone.length>90||
    x.fromDay!==from||x.toDay!==to||
    !groupValid(x.summary,'ALL')||!Array.isArray(x.items)||
    x.items.length!==sources.length||
    !sources.every((source,i)=>groupValid(x.items?.[i],source)))
    throw Error('shape');
  const rows=x.items as Group[],summary=x.summary as Group;
  if(!(['total','new','qualified','booked','lost'] as const)
    .every(k=>summary[k]===rows.reduce((n,row)=>n+row[k],0))||
    !reasons.every(k=>summary.lossReasons[k]===
      rows.reduce((n,row)=>n+row.lossReasons[k],0)))
    throw Error('inconsistent');
  return x as Report;
}

export function AutoCrmFunnel(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today);
  const [to,setTo]=React.useState(today);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'error'>('idle');
  const [report,setReport]=React.useState<Report|null>(null);
  const controller=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>controller.current?.abort(),[]);
  function clear(){controller.current?.abort();setReport(null);setStatus('idle');}
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const canLoad=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  async function load(){
    if(!canLoad)return;
    controller.current?.abort();
    const pending=new AbortController();controller.current=pending;
    setReport(null);setStatus('loading');
    const query=new URLSearchParams({fromDay:from,toDay:to});
    const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/crm/funnel?'+query;
    try{
      const response=await fetch(url,{
        credentials:'include',cache:'no-store',signal:pending.signal,
      });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(!response.ok)throw Error('http');
      const parsed=parse(await response.json(),from,to);
      if(!pending.signal.aborted){setReport(parsed);setStatus('ready');}
    }catch{if(!pending.signal.aborted)setStatus('error');}
  }
  return <section aria-label="Управленческая воронка CRM (dev)">
    <h1>Воронка обращений CRM</h1>
    <p>Только синтетический тестовый контур. Выберите филиал и даты
      <strong> создания обращений</strong> по местному часовому поясу филиала.
      Отчёт показывает их <strong>текущий последний этап</strong>, а не этап в день
      обращения. Отозванные подтверждения клиент–автомобиль не учитываются.
      Доля «Записан» не означает приезд, оплату или выручку.
      Персональные данные и идентификаторы клиентов не отображаются.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{clear();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{clear();setLoc(e.target.value);}}/></label>
    <label>С даты создания <input type="date" aria-label="С даты создания"
      value={from} onChange={e=>{clear();setFrom(e.target.value);}}/></label>
    <label>По дату создания <input type="date" aria-label="По дату создания"
      value={to} onChange={e=>{clear();setTo(e.target.value);}}/></label>
    <p>Диапазон: от 1 до 31 календарного дня включительно.</p>
    <button type="button" disabled={!canLoad||status==='loading'}
      onClick={()=>void load()}>Построить воронку CRM</button>
    {status==='loading'&&<p role="status">Подсчитываются обращения филиала…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён: требуется роль руководителя данного филиала.</p>}
    {status==='invalid'&&<p role="alert">Период отчёта некорректен.</p>}
    {status==='error'&&<p role="alert">Не удалось получить воронку CRM. Повторите запрос.</p>}
    {status==='ready'&&report&&<section aria-label="Результаты воронки CRM">
      <p>Часовой пояс филиала: {report.timeZone}. Основа: действующие
        подтверждённые обращения, созданные за выбранный период.</p>
      <p role="status">Всего обращений: {fmt(report.summary.total)}.
        Записан: {fmt(report.summary.booked)}.
        Доля записанных: {percent(report.summary.bookedBasisPoints)}.</p>
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Текущие этапы по источникам обращений</caption>
          <thead><tr><th scope="col">Источник</th><th scope="col">Всего</th>
            <th scope="col">Новое</th><th scope="col">Обработано</th>
            <th scope="col">Записан</th><th scope="col">Отказ</th>
            <th scope="col">Доля записанных</th></tr></thead>
          <tbody>{[report.summary,...report.items].map(r=><tr key={r.sourceCode}>
            <th scope="row">{sourceNames[r.sourceCode]}</th>
            <td>{fmt(r.total)}</td><td>{fmt(r.new)}</td>
            <td>{fmt(r.qualified)}</td><td>{fmt(r.booked)}</td>
            <td>{fmt(r.lost)}</td><td>{percent(r.bookedBasisPoints)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Причины текущих отказов по источникам</caption>
          <thead><tr><th scope="col">Источник</th>
            {reasons.map(r=><th scope="col" key={r}>{reasonNames[r]}</th>)}
          </tr></thead>
          <tbody>{[report.summary,...report.items].map(r=><tr key={r.sourceCode}>
            <th scope="row">{sourceNames[r.sourceCode]}</th>
            {reasons.map(reason=><td key={reason}>
              {fmt(r.lossReasons[reason])}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
      {report.summary.total===0&&<p>Подтверждённых обращений за период нет.</p>}
    </section>}
  </section>;
}
