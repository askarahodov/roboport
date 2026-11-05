import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const month=/^\d{4}-(0[1-9]|1[0-2])$/;
const PAGE=25;
type Class='A'|'B'|'C'|'X'|'Y'|'Z'|null;
type Counts={A:number;B:number;C:number;X:number;Y:number;Z:number;none:number};
type Item={
  skuId:string;article:string;grossIssued:number;grossReturned:number;
  netPeriod:number;monthlyIssued:number[];abc:Class;xyz:Class;
  variationBasisPoints:number|null;
};
type Summary={skuCount:number;activeSkus:number;grossIssued:number;
  grossReturned:number;netPeriod:number;classCounts:Counts};
type Report={
  syntheticOnly:true;basis:'GROSS_ISSUE_UNITS_CLOSED_MONTHS';moneyValuation:false;
  timeZone:string;fromMonth:string;toMonth:string;months:string[];
  pageSize:25;offset:number;nextOffset:number|null;
  summary:Summary;items:Item[];
};
const countKeys=['A','B','C','X','Y','Z','none'] as const;
const metricKeys=['skuCount','activeSkus','grossIssued','grossReturned'] as const;
const localMonth=(index:number)=>{
  const year=Math.floor(index/12),monthIndex=index%12;
  return String(year).padStart(4,'0')+'-'+String(monthIndex+1).padStart(2,'0');
};
const nowIndex=()=>{
  const now=new Date();
  return now.getUTCFullYear()*12+now.getUTCMonth();
};
const initialMonths=()=>{
  const final=nowIndex()-1;
  return {start:localMonth(final-5),end:localMonth(final)};
};
const monthIndex=(value:string)=>Number(value.slice(0,4))*12+Number(value.slice(5))-1;
const fmt=(value:number)=>value.toLocaleString('ru-RU');
const cv=(value:number|null)=>value===null?'—':
  (value/100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' %';

function isInteger(value:unknown):value is number{
  return typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;
}
function parse(raw:unknown,from:string,to:string,offset:number):Report{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as Partial<Report>;
  const expected=Array.from({length:monthIndex(to)-monthIndex(from)+1},
    (_,i)=>localMonth(monthIndex(from)+i));
  if(x.syntheticOnly!==true||x.basis!=='GROSS_ISSUE_UNITS_CLOSED_MONTHS'||
    x.moneyValuation!==false||typeof x.timeZone!=='string'||!x.timeZone||
    x.timeZone.length>100||x.fromMonth!==from||x.toMonth!==to||
    x.pageSize!==PAGE||x.offset!==offset||
    !Array.isArray(x.months)||x.months.length!==expected.length||
    !expected.every((label,i)=>x.months?.[i]===label)||
    !x.summary||!Array.isArray(x.items)||x.items.length>PAGE)
    throw Error('shape');
  const summary=x.summary as Summary;
  if(!metricKeys.every(k=>isInteger(summary[k]))||
    !Number.isSafeInteger(summary.netPeriod)||
    summary.netPeriod!==summary.grossIssued-summary.grossReturned||
    summary.activeSkus>summary.skuCount||
    summary.skuCount>10000||
    !summary.classCounts||
    !countKeys.every(k=>isInteger(summary.classCounts[k]))||
    summary.classCounts.A+summary.classCounts.B+
      summary.classCounts.C+summary.classCounts.none!==summary.skuCount||
    summary.classCounts.X+summary.classCounts.Y+
      summary.classCounts.Z+summary.classCounts.none!==summary.skuCount||
    summary.activeSkus!==summary.skuCount-summary.classCounts.none)
    throw Error('summary');
  if(!(x.nextOffset===null||
    (isInteger(x.nextOffset)&&x.nextOffset===offset+PAGE&&
      x.nextOffset<summary.skuCount))||
    (offset+PAGE<summary.skuCount)!==(x.nextOffset!==null)||
    x.items.length!==Math.min(PAGE,Math.max(0,summary.skuCount-offset)))
    throw Error('page');
  const known=new Set<string>();
  for(const row of x.items){
    if(!row||typeof row!=='object'||typeof row.skuId!=='string'||
      !uuid.test(row.skuId)||known.has(row.skuId)||
      typeof row.article!=='string'||row.article.length<1||row.article.length>80||
      !isInteger(row.grossIssued)||!isInteger(row.grossReturned)||
      !Number.isSafeInteger(row.netPeriod)||
      row.netPeriod!==row.grossIssued-row.grossReturned||
      !Array.isArray(row.monthlyIssued)||
      row.monthlyIssued.length!==expected.length||
      !row.monthlyIssued.every(isInteger)||
      row.monthlyIssued.reduce((sum,n)=>sum+n,0)!==row.grossIssued||
      (row.grossIssued===0?
        row.abc!==null||row.xyz!==null||row.variationBasisPoints!==null:
        !['A','B','C'].includes(row.abc??'')||
        !['X','Y','Z'].includes(row.xyz??'')||
        !isInteger(row.variationBasisPoints)))
      throw Error('item');
    known.add(row.skuId);
  }
  return x as Report;
}

/** Manager-only synthetic operational ABC/XYZ, never a monetary profitability report. */
export function AutoStockAbcXyz(){
  const defaults=React.useMemo(initialMonths,[]);
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(defaults.start);
  const [to,setTo]=React.useState(defaults.end);
  const [offset,setOffset]=React.useState(0);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<
    'idle'|'loading'|'ready'|'denied'|'invalid'|'capacity'|'error'
  >('idle');
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  function clear(){active.current?.abort();setReport(null);setStatus('idle');setOffset(0);}
  const span=month.test(from)&&month.test(to)?monthIndex(to)-monthIndex(from)+1:NaN;
  const valid=uuid.test(org)&&uuid.test(loc)&&Number.isInteger(span)&&
    span>=3&&span<=12;
  // Closed-month eligibility depends on the branch IANA time zone, not UTC.
  // The scoped server checks this and returns 422 when a local month is open.
  async function load(targetOffset:number){
    if(!valid)return;
    active.current?.abort();
    const controller=new AbortController();
    active.current=controller;
    setReport(null);setStatus('loading');
    const query=new URLSearchParams({
      fromMonth:from,toMonth:to,offset:String(targetOffset),
    });
    const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/stock-abc-xyz?'+query;
    try{
      const response=await fetch(url,{
        credentials:'include',cache:'no-store',signal:controller.signal,
      });
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(response.status===409){setStatus('capacity');return;}
      if(!response.ok)throw Error('http');
      const result=parse(await response.json(),from,to,targetOffset);
      if(!controller.signal.aborted){setOffset(targetOffset);setReport(result);setStatus('ready');}
    }catch{if(!controller.signal.aborted)setStatus('error');}
  }
  return <section aria-label="Операционный ABC XYZ склада (dev)">
    <h1>ABC/XYZ-анализ выдач запчастей</h1>
    <p>Только синтетический стенд. Анализируются <strong>количества реально
      зарегистрированных выдач ISSUE</strong> за 3–12 полностью завершённых
      календарных месяцев филиала. Возвраты RETURN показаны отдельно и не
      уменьшают месячную активность для классификации XYZ. Не используются
      резервы, предложения поставщиков, цены, валюта или себестоимость.
      Сравнение количества разных SKU — предварительный операционный
      ориентир, а <strong>не финансовый ABC, прогноз спроса или рекомендация закупки</strong>.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{clear();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{clear();setLoc(e.target.value);}}/></label>
    <label>С месяца <input type="month" aria-label="С месяца" value={from}
      onChange={e=>{clear();setFrom(e.target.value);}}/></label>
    <label>По месяц <input type="month" aria-label="По месяц" value={to}
      onChange={e=>{clear();setTo(e.target.value);}}/></label>
    <p>Методика: A/B/C по накопленной доле количества выдач
      (до 80% / до 95% / остальные), X/Y/Z по коэффициенту
      вариации месячных выдач (до 10% / до 25% / выше 25%).
      SKU без выдач — без класса.</p>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load(0)}>Построить ABC/XYZ</button>
    {status==='loading'&&<p role="status">Расчёт выдач и регулярности…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён: требуется руководитель филиала.</p>}
    {status==='invalid'&&<p role="alert">Нужны от 3 до 12 завершённых месяцев и действующие UUID.</p>}
    {status==='capacity'&&<p role="alert">В филиале слишком много SKU для этого отчёта.
      Ничего не было скрыто: расчёт прекращён целиком.</p>}
    {status==='error'&&<p role="alert">Не удалось получить ABC/XYZ. Повторите запрос.</p>}
    {status==='ready'&&report&&<section aria-label="Результаты ABC XYZ">
      <p>Период: {report.fromMonth} — {report.toMonth}, зона {report.timeZone}.
        Всего SKU: {fmt(report.summary.skuCount)}, с выдачами:
        {' '}{fmt(report.summary.activeSkus)}. Выдано: {fmt(report.summary.grossIssued)};
        возвращено: {fmt(report.summary.grossReturned)};
        разница за период: {fmt(report.summary.netPeriod)} единиц.</p>
      <p role="status">ABC: A {fmt(report.summary.classCounts.A)},
        B {fmt(report.summary.classCounts.B)},
        C {fmt(report.summary.classCounts.C)}.
        XYZ: X {fmt(report.summary.classCounts.X)},
        Y {fmt(report.summary.classCounts.Y)},
        Z {fmt(report.summary.classCounts.Z)}.
        Без выдач: {fmt(report.summary.classCounts.none)}.</p>
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Все позиции склада по количеству выдач, 25 на страницу</caption>
          <thead><tr><th scope="col">Артикул</th><th scope="col">Выдано</th>
            <th scope="col">Возвращено</th><th scope="col">Разница</th>
            <th scope="col">ABC</th><th scope="col">XYZ</th>
            <th scope="col">Вариация</th>
            {report.months.map(m=><th scope="col" key={m}>{m}</th>)}
          </tr></thead>
          <tbody>{report.items.map(row=><tr key={row.skuId}>
            <th scope="row">{row.article}</th>
            <td>{fmt(row.grossIssued)}</td><td>{fmt(row.grossReturned)}</td>
            <td>{fmt(row.netPeriod)}</td><td>{row.abc??'—'}</td>
            <td>{row.xyz??'—'}</td><td>{cv(row.variationBasisPoints)}</td>
            {row.monthlyIssued.map((n,i)=><td key={report.months[i]}>{fmt(n)}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
      {report.summary.skuCount===0&&<p>Позиций склада в филиале пока нет.</p>}
      {report.items.length===0&&report.summary.skuCount>0&&
        <p>На этой странице позиций нет.</p>}
      <nav aria-label="Страницы ABC XYZ">
        <button type="button" disabled={report.offset===0}
          onClick={()=>void load(Math.max(0,report.offset-PAGE))}>Предыдущая страница</button>
        <span>Страница {Math.floor(report.offset/PAGE)+1}</span>
        <button type="button" disabled={report.nextOffset===null}
          onClick={()=>void load(report.nextOffset!)}>Следующая страница</button>
      </nav>
    </section>}
  </section>;
}
