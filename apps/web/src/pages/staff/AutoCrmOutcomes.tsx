import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const sources=['WEB','PHONE','WALK_IN'] as const;
const titles:Record<string,string>={
  ALL:'Все источники',WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение',
};
type Row={sourceCode:string;bookedLeads:number;closedTechnical:number;
  simSettledMinor:string;simRefundMinor:string};
type Report={
  syntheticOnly:true;realMoney:false;releaseAllowed:false;
  revenueAvailable:false;basis:'LEAD_COHORT_CLOSED_TECHNICAL_SIM_TREASURY';
  timeZone:string;currency:'RUB';fromDay:string;toDay:string;
  summary:Row;items:Row[];
};
const today=()=>new Date().toISOString().slice(0,10);
const money=(minor:string)=>{
  const n=BigInt(minor);
  return (n/100n).toLocaleString('ru-RU')+','+
    (n%100n).toString().padStart(2,'0')+' ₽';
};
const count=(n:number)=>n.toLocaleString('ru-RU');
const numeric=(raw:unknown)=>typeof raw==='string'&&
  /^(0|[1-9]\d{0,31})$/.test(raw);
function isRow(raw:unknown,source:string):raw is Row{
  if(!raw||typeof raw!=='object')return false;
  const row=raw as Partial<Row>;
  return row.sourceCode===source&&
    typeof row.bookedLeads==='number'&&Number.isSafeInteger(row.bookedLeads)&&
    row.bookedLeads>=0&&typeof row.closedTechnical==='number'&&
    Number.isSafeInteger(row.closedTechnical)&&row.closedTechnical>=0&&
    row.closedTechnical<=row.bookedLeads&&
    numeric(row.simSettledMinor)&&numeric(row.simRefundMinor);
}
function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('invalid');
  const r=raw as Partial<Report>;
  if(r.syntheticOnly!==true||r.realMoney!==false||
    r.releaseAllowed!==false||r.revenueAvailable!==false||
    r.basis!=='LEAD_COHORT_CLOSED_TECHNICAL_SIM_TREASURY'||
    r.currency!=='RUB'||typeof r.timeZone!=='string'||!r.timeZone||
    r.timeZone.length>90||r.fromDay!==from||r.toDay!==to||
    !isRow(r.summary,'ALL')||!Array.isArray(r.items)||
    r.items.length!==3||!sources.every((source,i)=>isRow(r.items?.[i],source)))
    throw Error('invalid');
  const rows=r.items as Row[],summary=r.summary as Row;
  if(summary.bookedLeads!==rows.reduce((sum,x)=>sum+x.bookedLeads,0)||
    summary.closedTechnical!==rows.reduce((sum,x)=>sum+x.closedTechnical,0)||
    !(['simSettledMinor','simRefundMinor'] as const).every(key=>
      BigInt(summary[key])===rows.reduce((sum,x)=>sum+BigInt(x[key]),0n)))
    throw Error('inconsistent');
  return r as Report;
}

export function AutoCrmOutcomes(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today);
  const [to,setTo]=React.useState(today);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'error'>('idle');
  const [data,setData]=React.useState<Report|null>(null);
  const [exportError,setExportError]=React.useState('');
  const [exporting,setExporting]=React.useState(false);
  const controller=React.useRef<AbortController|null>(null);
  const exportController=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{controller.current?.abort();exportController.current?.abort();},[]);
  const reset=()=>{
    controller.current?.abort();exportController.current?.abort();
    setData(null);setStatus('idle');setExportError('');setExporting(false);
  };
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  const url=()=>{
    const query=new URLSearchParams({fromDay:from,toDay:to});
    return '/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/crm/outcomes?'+query;
  };
  async function load(){
    if(!valid)return;
    controller.current?.abort();
    const pending=new AbortController();controller.current=pending;
    setData(null);setStatus('loading');setExportError('');
    try{
      const response=await fetch(url(),{
        cache:'no-store',credentials:'include',signal:pending.signal,
      });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(!response.ok)throw Error('http');
      const report=parse(await response.json(),from,to);
      if(!pending.signal.aborted){setData(report);setStatus('ready');}
    }catch{if(!pending.signal.aborted)setStatus('error');}
  }
  async function exportCsv(){
    if(!data||status!=='ready'||exporting)return;
    exportController.current?.abort();
    const pending=new AbortController();exportController.current=pending;
    setExporting(true);setExportError('');
    try{
      const response=await fetch(url().replace('/crm/outcomes?','/crm/outcomes/export?'),{
        cache:'no-store',credentials:'include',signal:pending.signal,
      });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setData(null);setStatus('denied');return;
      }
      if(response.status===422){setData(null);setStatus('invalid');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('http');
      const blob=await response.blob();
      if(pending.signal.aborted)return;
      const objectUrl=URL.createObjectURL(blob);
      const a=document.createElement('a');
      a.href=objectUrl;a.download='auto-crm-outcomes.csv';
      document.body.appendChild(a);a.click();a.remove();
      window.setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
    }catch{
      if(!pending.signal.aborted)setExportError('Не удалось скачать CSV.');
    }finally{if(!pending.signal.aborted)setExporting(false);}
  }
  return <section aria-label="Техническая конверсия CRM (dev)">
    <h1>CRM: запись → закрытие → тестовые расчёты</h1>
    <p>Только синтетический стенд. Это <strong>не выручка</strong>, не фискальные
      платежи и не передача автомобиля. Учитываются только подтверждённые
      клиентские связи и <strong>текущие</strong> лиды со статусом «Записан»,
      привязанные однозначно к одному заказ-наряду с событиями POSTED и
      CLOSED_TECHNICAL. Авансы без зачёта исключаются. Неоднозначные связи
      не получают денежной атрибуции. Период — дата создания лида в часовом
      поясе филиала, от 1 до 31 дня.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>С даты создания <input type="date" aria-label="С даты создания"
      value={from} onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>По дату создания <input type="date" aria-label="По дату создания"
      value={to} onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Проверить техническую конверсию</button>
    {status==='loading'&&<p role="status">Сверка заказ-нарядов и расчётов…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён: проверьте роль, филиал и права на цены документов.</p>}
    {status==='invalid'&&<p role="alert">Проверьте период и идентификаторы.</p>}
    {status==='error'&&<p role="alert">Не удалось построить отчёт. Повторите попытку.</p>}
    {exportError&&<p role="alert">{exportError}</p>}
    {status==='ready'&&data&&<section aria-label="Результаты технической конверсии">
      <p>Часовой пояс: {data.timeZone}. Денежные показатели — только SIM,
        без реальной оплаты и без признака юридического завершения.</p>
      <p role="status">Записано лидов: {count(data.summary.bookedLeads)}.
        Технически закрыто: {count(data.summary.closedTechnical)}.
        Тестовый зачёт: {money(data.summary.simSettledMinor)}.</p>
      <button type="button" disabled={exporting}
        onClick={()=>void exportCsv()}>
        {exporting?'Готовим CSV…':'Скачать техническую сверку CSV'}
      </button>
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Синтетическая конверсия по источнику лида</caption>
          <thead><tr><th scope="col">Источник</th><th scope="col">Записано</th>
            <th scope="col">Закрыто технически</th>
            <th scope="col">Зачтено SIM, НЕ выручка</th>
            <th scope="col">Возвраты SIM</th></tr></thead>
          <tbody>{[data.summary,...data.items].map(row=><tr key={row.sourceCode}>
            <th scope="row">{titles[row.sourceCode]}</th>
            <td>{count(row.bookedLeads)}</td><td>{count(row.closedTechnical)}</td>
            <td>{money(row.simSettledMinor)}</td><td>{money(row.simRefundMinor)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {data.summary.bookedLeads===0&&<p>Подтверждённых записанных лидов за период нет.</p>}
    </section>}
  </section>;
}
