import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const date=/^\d{4}-\d{2}-\d{2}$/;
const today=()=>new Date().toISOString().slice(0,10);
const safe=(x:unknown):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0;
type Shift={shiftId:string;startsAt:string;endsAt:string;overlapSeconds:number};
type Person={
  mechanicId:string;recordedMinutes:number;scheduledSeconds:number;shiftCount:number;
  recordedToScheduledBasisPoints:number|null;
  status:'COMPARABLE'|'NO_ACTIVE_SHIFT'|'NO_CLOSED_LABOR';shifts:Shift[];
};
type Comparison={
  basis:'ACTIVE_SHIFTS_VS_CLOSED_TECHNICAL_LABOR';
  isAttendance:false;isPayroll:false;isEfficiencyScore:false;limitedToShifts:500;
  summary:{mechanicCount:number;shiftedMechanicCount:number;shiftCount:number;
    scheduledSeconds:number;recordedMinutes:number};
  items:Person[];
};
type Report={
  syntheticOnly:true;realMoney:false;payrollAvailable:false;releaseAllowed:false;
  basis:'CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES';
  fromDay:string;toDay:string;timeZone:string;
  summary:{mechanicCount:number;workOrderCount:number;serviceCount:number;actualMinutes:number};
  items:Array<{mechanicId:string;actualMinutes:number}>;
  shiftComparison:Comparison;
};

function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const r=raw as Report,c=r.shiftComparison;
  if(r.syntheticOnly!==true||r.realMoney!==false||r.payrollAvailable!==false||
    r.releaseAllowed!==false||r.basis!=='CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES'||
    r.fromDay!==from||r.toDay!==to||typeof r.timeZone!=='string'||!r.timeZone||
    !r.summary||!safe(r.summary.actualMinutes)||!Array.isArray(r.items)||
    !c||c.basis!=='ACTIVE_SHIFTS_VS_CLOSED_TECHNICAL_LABOR'||
    c.isAttendance!==false||c.isPayroll!==false||c.isEfficiencyScore!==false||
    c.limitedToShifts!==500||!Array.isArray(c.items)||c.items.length>1000||
    !c.summary||![c.summary.mechanicCount,c.summary.shiftedMechanicCount,
      c.summary.shiftCount,c.summary.scheduledSeconds,c.summary.recordedMinutes].every(safe))
    throw Error('shape');
  const labor=new Map<string,number>();
  for(const entry of r.items){
    if(!entry||!uuid.test(entry.mechanicId)||!safe(entry.actualMinutes)||
      labor.has(entry.mechanicId))throw Error('labor');
    labor.set(entry.mechanicId,entry.actualMinutes);
  }
  const people=new Set<string>(),shifts=new Set<string>();
  let scheduled=0,recorded=0,shifted=0;
  for(const p of c.items){
    if(!p||!uuid.test(p.mechanicId)||people.has(p.mechanicId)||
      ![p.recordedMinutes,p.scheduledSeconds,p.shiftCount].every(safe)||
      !Array.isArray(p.shifts)||p.shifts.length!==p.shiftCount||
      p.recordedMinutes!==(labor.get(p.mechanicId)??0))throw Error('person');
    people.add(p.mechanicId);
    let duration=0;
    for(const shift of p.shifts){
      if(!shift||!uuid.test(shift.shiftId)||shifts.has(shift.shiftId)||
        typeof shift.startsAt!=='string'||typeof shift.endsAt!=='string'||
        !Number.isFinite(Date.parse(shift.startsAt))||
        !Number.isFinite(Date.parse(shift.endsAt))||
        Date.parse(shift.startsAt)>=Date.parse(shift.endsAt)||
        !safe(shift.overlapSeconds)||shift.overlapSeconds<1||
        shift.overlapSeconds>(Date.parse(shift.endsAt)-Date.parse(shift.startsAt))/1000)
        throw Error('shift');
      shifts.add(shift.shiftId);duration+=shift.overlapSeconds;
    }
    const ratio=duration>0?Math.floor((p.recordedMinutes*60*10000+duration/2)/duration):null;
    const status=duration===0?'NO_ACTIVE_SHIFT':
      p.recordedMinutes===0?'NO_CLOSED_LABOR':'COMPARABLE';
    if(!Number.isSafeInteger(duration)||p.scheduledSeconds!==duration||
      p.recordedToScheduledBasisPoints!==ratio||p.status!==status)throw Error('sums');
    scheduled+=duration;recorded+=p.recordedMinutes;
    if(duration>0)shifted++;
  }
  if([...labor.keys()].some(id=>!people.has(id))||
    !Number.isSafeInteger(scheduled)||!Number.isSafeInteger(recorded)||
    c.summary.mechanicCount!==people.size||
    c.summary.shiftedMechanicCount!==shifted||
    c.summary.shiftCount!==shifts.size||
    c.summary.scheduledSeconds!==scheduled||
    c.summary.recordedMinutes!==recorded||
    recorded!==r.summary.actualMinutes)throw Error('summary');
  return r;
}

const statusText:Record<Person['status'],string>={
  COMPARABLE:'Есть смена и закрытый труд',
  NO_ACTIVE_SHIFT:'Нет действующей смены в периоде',
  NO_CLOSED_LABOR:'Нет технически закрытого труда',
};

export function AutoMechanicShiftComparison(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today),[to,setTo]=React.useState(today);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'limit'|'error'>('idle');
  const [busy,setBusy]=React.useState(false),[exportError,setExportError]=React.useState('');
  const active=React.useRef<AbortController|null>(null);
  const exporting=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{active.current?.abort();exporting.current?.abort();},[]);
  function clear(){
    active.current?.abort();exporting.current?.abort();
    setReport(null);setStatus('idle');setExportError('');setBusy(false);
  }
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&date.test(from)&&date.test(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  const url=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/reports/mechanic-productivity/shift-comparison?'+
    new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    active.current?.abort();exporting.current?.abort();
    const control=new AbortController();active.current=control;
    setReport(null);setStatus('loading');setExportError('');
    try{
      const response=await fetch(url(),{credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(response.status===409){setStatus('limit');return;}
      if(!response.ok)throw Error('HTTP');
      const data=parse(await response.json(),from,to);
      if(!control.signal.aborted){setReport(data);setStatus('ready');}
    }catch{
      if(!control.signal.aborted)setStatus('error');
    }
  }
  async function download(){
    if(!report||busy)return;
    exporting.current?.abort();
    const control=new AbortController();exporting.current=control;
    setBusy(true);setExportError('');
    try{
      const response=await fetch(url().replace('/shift-comparison?','/shift-comparison/export?'),{
        credentials:'include',cache:'no-store',signal:control.signal,
      });
      if(control.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setReport(null);setStatus('denied');return;
      }
      if(response.status===409){setReport(null);setStatus('limit');return;}
      if(response.status===422){setReport(null);setStatus('invalid');return;}
      if(!response.ok||response.headers.get('content-type')?.split(';')[0]!=='text/csv')
        throw Error('HTTP');
      const blob=await response.blob();
      if(control.signal.aborted||blob.size>1024*1024||blob.size===0)throw Error('size');
      const address=URL.createObjectURL(blob);
      try{
        const anchor=document.createElement('a');
        anchor.href=address;anchor.download='auto-mechanic-shift-comparison.csv';
        document.body.appendChild(anchor);anchor.click();anchor.remove();
      }finally{URL.revokeObjectURL(address);}
    }catch{
      if(!control.signal.aborted){
        setReport(null);setStatus('error');
        setExportError('Не удалось экспортировать сверку; обновите права и данные.');
      }
    }finally{if(exporting.current===control)setBusy(false);}
  }
  const data=report?.shiftComparison;
  return <section aria-label="Сопоставление смен и закрытых работ (dev)"
    style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}}> 
    <h1>Смены и проверенная выработка механиков</h1>
    <p>Синтетическая сверка: действующие смены в локальном периоде и
      минуты работ, попавших в технически закрытые заказ-наряды по дате закрытия.
      Эти события имеют <strong>разные временные основания</strong>.
      Отношение не измеряет присутствие, KPI, зарплату, выручку или эффективность.
      Доступ только MANAGER своего филиала с неограниченной историей HANDOVER/READ.
      Отзыв прав немедленно скрывает данные.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{clear();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{clear();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода" value={from}
      onChange={e=>{clear();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода" value={to}
      onChange={e=>{clear();setTo(e.target.value);}}/></label>
    <button type="button" onClick={()=>void load()} disabled={!valid||status==='loading'}>
      Сопоставить смены и труд</button>
    {!valid&&<p>Укажите организацию, филиал и период от 1 до 31 дня.</p>}
    {status==='loading'&&<p role="status">Загружаем сверку…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или отозван. Сведения очищены.</p>}
    {status==='invalid'&&<p role="alert">Неверный период. Используйте 1–31 день.</p>}
    {status==='limit'&&<p role="alert">Конфликт или более 500 записей труда/смен; сократите период и повторите.</p>}
    {status==='error'&&<p role="alert">Не удалось сформировать проверяемую сверку.</p>}
    {report&&data&&status==='ready'&&<section aria-label="Результаты сверки смен">
      <p role="status">Смены: {data.summary.shiftCount};
        механики: {data.summary.mechanicCount};
        часы смен: {(data.summary.scheduledSeconds/3600).toFixed(2)};
        подтверждённый труд: {data.summary.recordedMinutes} минут.
        Филиальная зона: {report.timeZone}.</p>
      <div className="table-scroll" role="region" aria-label="Прокручиваемая таблица сверки"
        tabIndex={0} style={{width:'100%',maxWidth:'100%',minWidth:0,overflowX:'auto'}}>
        <table aria-label="Сопоставление смен и труда">
        <thead><tr><th>Механик UUID</th><th>Действующих смен</th>
          <th>Часы смен</th><th>Труд по закрытиям, минут</th>
          <th>Отношение, %</th><th>Ограничение</th></tr></thead>
        <tbody>{data.items.map(x=><tr key={x.mechanicId}>
          <td><code style={{overflowWrap:'anywhere',wordBreak:'break-all'}}>{x.mechanicId}</code></td><td>{x.shiftCount}</td>
          <td>{(x.scheduledSeconds/3600).toFixed(2)}</td>
          <td>{x.recordedMinutes}</td>
          <td>{x.recordedToScheduledBasisPoints===null?'—':
            (x.recordedToScheduledBasisPoints/100).toFixed(2)}</td>
          <td>{statusText[x.status]}</td>
        </tr>)}</tbody>
      </table></div>
      {data.items.length===0&&<p>За выбранный период нет действующих смен или
        технически закрытого труда.</p>}
      {data.items.map(x=><details key={x.mechanicId}>
        <summary style={{overflowWrap:'anywhere'}}>Источники смен: <code style={{wordBreak:'break-all'}}>{x.mechanicId}</code></summary>
        <ul>{x.shifts.map(y=><li key={y.shiftId} style={{minWidth:0,overflowWrap:'anywhere'}}>
          <code style={{wordBreak:'break-all'}}>{y.shiftId}</code> ·
          {y.startsAt} — {y.endsAt}: {y.overlapSeconds} секунд пересечения</li>)}</ul>
      </details>)}
      <button type="button" disabled={busy} onClick={()=>void download()}>
        Скачать сверку CSV</button>
      {exportError&&<p role="alert">{exportError}</p>}
      <p>CSV не содержит ФИО, VIN, клиентских данных или денежных сумм.
        Это не табель посещаемости и не расчёт зарплаты.</p>
    </section>}
  </section>;
}
