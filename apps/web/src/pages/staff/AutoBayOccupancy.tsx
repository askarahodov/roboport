import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
type Row={
  localDay:string;daySeconds:number;activeAppointments:number;
  occupiedSeconds:number;calendarCapacitySeconds:number;
  occupancyBasisPoints:number|null;
};
type Report={
  syntheticOnly:true;basis:'CURRENT_BOOKINGS_CALENDAR_DAY';
  timeZone:string;bayCount:number;items:Row[];
};
const isoDate=()=>new Date().toISOString().slice(0,10);
const hours=(n:number)=>(n/3600).toLocaleString('ru-RU',{maximumFractionDigits:2});
function valid(value:unknown):value is Report{
  if(!value||typeof value!=='object')return false;
  const r=value as Report;
  return r.syntheticOnly===true&&r.basis==='CURRENT_BOOKINGS_CALENDAR_DAY'&&
    typeof r.timeZone==='string'&&r.timeZone.length>0&&
    Number.isSafeInteger(r.bayCount)&&r.bayCount>=0&&
    Array.isArray(r.items)&&r.items.length<=31&&
    r.items.every(x=>x&&typeof x.localDay==='string'&&day.test(x.localDay)&&
      [x.daySeconds,x.activeAppointments,x.occupiedSeconds,x.calendarCapacitySeconds]
        .every(v=>Number.isSafeInteger(v)&&v>=0)&&
      (x.occupancyBasisPoints===null||
        Number.isSafeInteger(x.occupancyBasisPoints)&&
        x.occupancyBasisPoints>=0&&x.occupancyBasisPoints<=10000));
}

/** Руководитель видит только агрегаты текущих бронирований; без VIN, PII и денег. */
export function AutoBayOccupancy(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(isoDate);
  const [to,setTo]=React.useState(isoDate);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'error'>('idle');
  const pending=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>pending.current?.abort(),[]);
  function clear(){
    pending.current?.abort();
    setReport(null);setStatus('idle');
  }
  function updateOrg(value:string){clear();setOrg(value);}
  function updateLoc(value:string){clear();setLoc(value);}
  function updateFrom(value:string){clear();setFrom(value);}
  function updateTo(value:string){clear();setTo(value);}
  const days=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const canLoad=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(days)&&days>=1&&days<=31;
  async function load(){
    if(!canLoad)return;
    pending.current?.abort();
    const controller=new AbortController();pending.current=controller;
    setReport(null);setStatus('loading');
    try{
      const url='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
        '/locations/'+encodeURIComponent(loc)+'/bay-occupancy?'+
        new URLSearchParams({fromDay:from,toDay:to});
      const response=await fetch(url,{credentials:'include',cache:'no-store',
        signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(!response.ok)throw Error('request');
      const parsed:unknown=await response.json();
      if(!valid(parsed)||parsed.items.length!==days||
        parsed.items[0]?.localDay!==from||parsed.items[parsed.items.length-1]?.localDay!==to)
        throw Error('shape');
      if(controller.signal.aborted)return;
      setReport(parsed);setStatus('ready');
    }catch{
      if(!controller.signal.aborted)setStatus('error');
    }
  }
  return <section aria-label="Загрузка постов автосервиса (dev)">
    <h1>Загрузка постов по дням</h1>
    <p>Только синтетический HTTPS-стенд. Отчёт показывает текущие активные записи,
      не историю финансовых операций и не фактическое время работы механиков.
      Доля считается от <strong>всех часов локальных календарных суток</strong>,
      включая нерабочие часы; доступность постов — на момент запроса.</p>
    <label>UUID организации <input aria-label="UUID организации"
      value={org} onChange={e=>updateOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала"
      value={loc} onChange={e=>updateLoc(e.target.value)}/></label>
    <label>Начало периода <input aria-label="Начало периода" type="date"
      value={from} onChange={e=>updateFrom(e.target.value)}/></label>
    <label>Конец периода <input aria-label="Конец периода" type="date"
      value={to} onChange={e=>updateTo(e.target.value)}/></label>
    <button type="button" disabled={!canLoad||status==='loading'}
      onClick={()=>void load()}>
      Сформировать отчёт</button>
    {!canLoad&&<p>Выберите организацию, филиал и период от 1 до 31 дня.</p>}
    {status==='loading'&&<p role="status">Расчёт загрузки…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён. Нужна действующая роль руководителя филиала.</p>}
    {status==='invalid'&&<p role="alert">Некорректный период. Проверьте даты.</p>}
    {status==='error'&&<p role="alert">Не удалось получить отчёт. Повторите запрос.</p>}
    {status==='ready'&&report&&<div>
      <p role="status">Расчёт выполнен · {report.timeZone} · доступно постов: {report.bayCount}.
        Это текущее состояние бронирований; перенос и отмена меняют следующий отчёт.</p>
      <div style={{overflowX:'auto'}}>
        <table><caption>Текущая занятость постов по локальным суткам</caption>
          <thead><tr><th scope="col">День</th><th scope="col">Записи</th>
            <th scope="col">Занято, ч</th><th scope="col">Всего посто-часов</th>
            <th scope="col">Доля</th></tr></thead>
          <tbody>{report.items.map(x=><tr key={x.localDay}>
            <th scope="row">{x.localDay}</th><td>{x.activeAppointments}</td>
            <td>{hours(x.occupiedSeconds)}</td>
            <td>{hours(x.calendarCapacitySeconds)}</td>
            <td>{x.occupancyBasisPoints===null?'Нет активных постов':
              (x.occupancyBasisPoints/100).toLocaleString('ru-RU',{
                maximumFractionDigits:2,
              })+' %'}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </div>}
  </section>;
}
