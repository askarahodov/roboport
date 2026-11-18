import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const today=()=>new Date().toISOString().slice(0,10);
const safe=(n:unknown):n is number=>typeof n==='number'&&Number.isSafeInteger(n)&&n>=0;
type LaborSource={laborEntryId:string;serviceId:string;actualMinutes:number};
type WorkOrder={
  workOrderId:string;closedDay:string;closedEventId:string;finishEventId:string;
  qcEventId:string;serviceCount:number;actualMinutes:number;serviceIds:string[];
  laborEntries:LaborSource[];
};
type Mechanic={
  mechanicId:string;workOrderCount:number;serviceCount:number;
  actualMinutes:number;orders:WorkOrder[];
};
type Report={
  syntheticOnly:true;realMoney:false;payrollAvailable:false;releaseAllowed:false;
  basis:'CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES';
  periodBasis:'CLOSED_TECHNICAL_LOCAL_DAY';
  timeZone:string;fromDay:string;toDay:string;activeMechanicsOnly:true;
  limitedToEntries:500;
  summary:{mechanicCount:number;workOrderCount:number;
    serviceCount:number;actualMinutes:number};
  items:Mechanic[];
};

function parse(raw:unknown,from:string,to:string):Report{
  if(!raw||typeof raw!=='object')throw Error('invalid');
  const r=raw as Partial<Report>;
  if(r.syntheticOnly!==true||r.realMoney!==false||
    r.payrollAvailable!==false||r.releaseAllowed!==false||
    r.basis!=='CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES'||
    r.periodBasis!=='CLOSED_TECHNICAL_LOCAL_DAY'||
    r.activeMechanicsOnly!==true||r.limitedToEntries!==500||
    r.fromDay!==from||r.toDay!==to||
    typeof r.timeZone!=='string'||r.timeZone.length<2||r.timeZone.length>100||
    !Array.isArray(r.items)||r.items.length>500||!r.summary||
    ![r.summary.mechanicCount,r.summary.workOrderCount,
      r.summary.serviceCount,r.summary.actualMinutes].every(safe))throw Error('invalid');
  const ids=new Set<string>(),uniqueOrders=new Set<string>();
  const immutable=new Map<string,string>(),sourceIds=new Set<string>();
  let minutes=0,services=0;
  for(const mechanic of r.items){
    if(!mechanic||typeof mechanic!=='object'||!uuid.test(mechanic.mechanicId)||
      ids.has(mechanic.mechanicId)||!Array.isArray(mechanic.orders)||
      ![mechanic.workOrderCount,mechanic.serviceCount,mechanic.actualMinutes].every(safe)||
      mechanic.orders.length!==mechanic.workOrderCount)throw Error('invalid');
    ids.add(mechanic.mechanicId);
    const seen=new Set<string>();
    let mechanicMinutes=0,mechanicServices=0;
    for(const order of mechanic.orders){
      if(!order||!uuid.test(order.workOrderId)||seen.has(order.workOrderId)||
        !day.test(order.closedDay)||order.closedDay<from||order.closedDay>to||
        ![order.closedEventId,order.finishEventId,order.qcEventId].every(x=>uuid.test(x))||
        !safe(order.actualMinutes)||!safe(order.serviceCount)||
        order.serviceCount<1||!Array.isArray(order.serviceIds)||
        !Array.isArray(order.laborEntries)||
        order.laborEntries.length!==order.serviceCount||
        order.serviceIds.length!==order.serviceCount)throw Error('invalid');
      const proof=[order.closedDay,order.closedEventId,order.finishEventId,order.qcEventId].join(':');
      if(immutable.has(order.workOrderId)&&immutable.get(order.workOrderId)!==proof)
        throw Error('mixed proof');
      immutable.set(order.workOrderId,proof);
      let verifiedMinutes=0;
      for(const [index,entry] of order.laborEntries.entries()){
        if(!entry||!uuid.test(entry.laborEntryId)||sourceIds.has(entry.laborEntryId)||
          !uuid.test(entry.serviceId)||!safe(entry.actualMinutes)||
          entry.actualMinutes<1||entry.actualMinutes>1440||
          order.serviceIds[index]!==entry.serviceId)throw Error('labor proof');
        sourceIds.add(entry.laborEntryId);
        verifiedMinutes+=entry.actualMinutes;
      }
      if(!Number.isSafeInteger(verifiedMinutes)||verifiedMinutes!==order.actualMinutes)
        throw Error('labor sum');
      seen.add(order.workOrderId);uniqueOrders.add(order.workOrderId);
      mechanicMinutes+=order.actualMinutes;
      mechanicServices+=order.serviceCount;
    }
    if(mechanicMinutes!==mechanic.actualMinutes||
      mechanicServices!==mechanic.serviceCount)throw Error('mismatch');
    minutes+=mechanicMinutes;services+=mechanicServices;
  }
  if(r.summary.mechanicCount!==ids.size||
    r.summary.workOrderCount!==uniqueOrders.size||
    r.summary.serviceCount!==services||r.summary.actualMinutes!==minutes||
    services>500||!Number.isSafeInteger(minutes))throw Error('mismatch');
  return r as Report;
}

export function AutoMechanicProductivity(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [from,setFrom]=React.useState(today);
  const [to,setTo]=React.useState(today);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'limit'|'error'>('idle');
  const [report,setReport]=React.useState<Report|null>(null);
  const [exportError,setExportError]=React.useState('');
  const [exporting,setExporting]=React.useState(false);
  const active=React.useRef<AbortController|null>(null);
  const download=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{active.current?.abort();download.current?.abort();},[]);
  const reset=()=>{
    active.current?.abort();download.current?.abort();
    setReport(null);setStatus('idle');setExporting(false);setExportError('');
  };
  const span=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&day.test(from)&&day.test(to)&&
    Number.isInteger(span)&&span>=1&&span<=31;
  const url=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/reports/mechanic-productivity?'+
    new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    active.current?.abort();
    const controller=new AbortController();active.current=controller;
    setReport(null);setStatus('loading');setExportError('');
    try{
      const response=await fetch(url(),{credentials:'include',cache:'no-store',
        signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(response.status===409){setStatus('limit');return;}
      if(!response.ok)throw Error('http');
      const data=parse(await response.json(),from,to);
      if(!controller.signal.aborted){setReport(data);setStatus('ready');}
    }catch{if(!controller.signal.aborted)setStatus('error');}
  }
  async function exportCsv(){
    if(!report||exporting)return;
    download.current?.abort();
    const controller=new AbortController();download.current=controller;
    setExportError('');setExporting(true);
    try{
      const response=await fetch(url().replace('/reports/mechanic-productivity?','/reports/mechanic-productivity/export?'),{
        credentials:'include',cache:'no-store',signal:controller.signal,
      });
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setReport(null);setStatus('denied');return;
      }
      if(response.status===409){setReport(null);setStatus('limit');return;}
      if(response.status===422){setReport(null);setStatus('invalid');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('bad export');
      const blob=await response.blob();
      if(controller.signal.aborted)return;
      const blobUrl=URL.createObjectURL(blob);
      const anchor=document.createElement('a');
      anchor.href=blobUrl;anchor.download='auto-mechanic-productivity.csv';
      document.body.appendChild(anchor);anchor.click();anchor.remove();
      window.setTimeout(()=>URL.revokeObjectURL(blobUrl),1000);
    }catch{if(!controller.signal.aborted)setExportError('Не удалось скачать CSV.');}
    finally{if(!controller.signal.aborted)setExporting(false);}
  }
  return <section aria-label="Выработка механиков (dev)">
    <h1>Проверяемая выработка механиков</h1>
    <p><strong>Только синтетический dev.</strong> Учитываются фактические минуты
      по заказ-нарядам с техническим закрытием, завершением работ и независимым
      QC PASS. Только сотрудники с действующим доступом и подтверждённые
      связи с автомобилем. Это <strong>не зарплата, не прибыль, не выручка</strong>
      и не юридически завершённый ремонт. Период — локальная дата
      события технического закрытия CLOSED_TECHNICAL, не дата завершения работ (1–31 день).</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода"
      value={from} onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода"
      value={to} onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Рассчитать выработку</button>
    {!valid&&<p>Нужны UUID организации/филиала и период до 31 дня.</p>}
    {status==='loading'&&<p role="status">Проверка технических закрытий и QC…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён: проверьте роль руководителя, филиал и права на документы.</p>}
    {status==='invalid'&&<p role="alert">Неверный период или идентификатор.</p>}
    {status==='limit'&&<p role="alert">За период более 500 строк выполненных работ. Сократите период.</p>}
    {status==='error'&&<p role="alert">Не удалось сформировать отчёт. Повторите запрос.</p>}
    {exportError&&<p role="alert">{exportError}</p>}
    {status==='ready'&&report&&<section aria-label="Результаты выработки">
      <p role="status">Механиков: {report.summary.mechanicCount}.
        Закрытых нарядов: {report.summary.workOrderCount}.
        Услуг: {report.summary.serviceCount}.
        Факт: {report.summary.actualMinutes} мин.</p>
      <p>Часовой пояс: {report.timeZone}. День отчёта — закрытие заказ-наряда; показаны только действующие сотрудники.
        При отзыве прав отчёт перечитывается, ранее показанные строки не гарантированы.
        UUID сотрудника — технический идентификатор без имени и ставки.</p>
      <button type="button" disabled={exporting}
        onClick={()=>void exportCsv()}>{exporting?'Готовим CSV…':'Скачать выработку CSV'}</button>
      {report.items.length===0&&<p>Подтверждённых закрытых работ за период нет.</p>}
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Фактические минуты с детализацией по заказ-нарядам</caption>
          <thead><tr><th scope="col">Механик UUID</th>
            <th scope="col">Заказ-наряд UUID</th><th scope="col">День закрытия</th><th scope="col">Услуг</th>
            <th scope="col">Факт, минут</th><th scope="col">Первичные события</th></tr></thead>
          <tbody>{report.items.flatMap(person=>person.orders.map(order=>
            <tr key={person.mechanicId+order.workOrderId}>
              <td>{person.mechanicId}</td><th scope="row">{order.workOrderId}</th>
              <td>{order.closedDay}</td><td>{order.serviceCount}</td><td>{order.actualMinutes}</td>
              <td><details><summary>Первичные записи труда</summary>
                <p>Техническое закрытие: {order.closedEventId}</p>
                <p>Завершение работ: {order.finishEventId}</p>
                <p>QC PASS: {order.qcEventId}</p>
                <ul>{order.laborEntries.map(entry=><li key={entry.laborEntryId}>
                  Запись труда: {entry.laborEntryId} · Услуга: {entry.serviceId}
                  {' '}· {entry.actualMinutes} мин
                </li>)}</ul></details></td>
            </tr>))}</tbody>
        </table>
      </div>
    </section>}
  </section>;
}
