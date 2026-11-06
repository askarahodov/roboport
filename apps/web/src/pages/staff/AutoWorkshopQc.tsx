import React from 'react';

type Inspection = { id:string; odometerKm:number; fuelPercent:number; tyresOk:boolean;
  lightsOk:boolean; fluidLeak:boolean; exteriorCode:string; createdAt:string };
type Event = { id:string; result:'PASS'|'FAIL'; findingCode:string; createdAt:string };
type Snapshot = { status:'NOT_INSPECTED'|'PENDING_QC'|'QC_FAILED'|'QC_PASSED';
  releaseAllowed:false; inspection:Inspection|null; qcEvents:Event[] };
type Pending = { path:string; body:string; key:string };
type QueueItem={orderId:string;status:'PENDING_QC'|'FAIL';lastFindingCode:string};
type QueueState={status:'idle'|'loading'|'ready'|'denied'|'error';
  items:QueueItem[];nextOrderId:string|null;cursor:string|null};
const emptyQueue=():QueueState=>({status:'idle',items:[],nextOrderId:null,cursor:null});
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function validate(raw:unknown):Snapshot {
  if(!raw||typeof raw!=='object')throw Error('invalid');
  const value=raw as Record<string,unknown>;
  if(!['NOT_INSPECTED','PENDING_QC','QC_FAILED','QC_PASSED'].includes(String(value.status))||
      value.releaseAllowed!==false || !Array.isArray(value.qcEvents)||value.qcEvents.length>50||
      !value.qcEvents.every((x:unknown)=>x!==null&&typeof x==='object'
        && 'id' in x&&typeof x.id==='string'&&uuid.test(x.id)
        && 'result' in x&&(x.result==='PASS'||x.result==='FAIL')
        && 'findingCode' in x&&typeof x.findingCode==='string'
        && 'createdAt' in x&&typeof x.createdAt==='string'))throw Error('invalid');
  if(value.inspection!==null && (!value.inspection||typeof value.inspection!=='object'||
    !('odometerKm' in value.inspection)||typeof value.inspection.odometerKm!=='number'||
    !('fuelPercent' in value.inspection)||typeof value.inspection.fuelPercent!=='number'||
    !('exteriorCode' in value.inspection)||typeof value.inspection.exteriorCode!=='string'))throw Error('invalid');
  return raw as Snapshot;
}

export function AutoWorkshopQc() {
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [loadedScope,setLoadedScope]=React.useState('');
  const [snapshot,setSnapshot]=React.useState<Snapshot|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [queue,setQueue]=React.useState<QueueState>(emptyQueue);
  const queueSequence=React.useRef(0);
  const currentBranch=React.useRef({org:'',loc:''});
  currentBranch.current={org:org.trim(),loc:loc.trim()};
  const [odometer,setOdometer]=React.useState('');
  const [fuel,setFuel]=React.useState('50');
  const [tyres,setTyres]=React.useState(false);
  const [lights,setLights]=React.useState(false);
  const [leak,setLeak]=React.useState(false);
  const [exterior,setExterior]=React.useState('UNCHECKED');
  const [qcResult,setQcResult]=React.useState<'PASS'|'FAIL'>('FAIL');
  const [finding,setFinding]=React.useState('REWORK');
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const valid=[org,loc,order].every(x=>uuid.test(x.trim()));
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())
    +'/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim());
  const ready=status==='ready'&&loadedScope===scope&&snapshot!==null;
  React.useEffect(()=>{
    setStatus(valid?'loading':'idle');setLoadedScope('');setSnapshot(null);setMessage('');setPending(null);
    if(!valid)return;
    const controller=new AbortController();
    void fetch(root+'/inspection-qc',{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async res=>{
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('unavailable');
        const data=validate(await res.json());
        if(!controller.signal.aborted){setSnapshot(data);setLoadedScope(scope);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,revision,valid]);
  async function execute(path:string,body:string,key:string) {
    if(!valid||busy||!ready)return;
    setBusy(true);setMessage('');
    const currentScope=scope;
    try {
      const csrf=await fetch('/api/v1/auth/csrf',
        {method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('CSRF');
      const value:unknown=await csrf.json();
      if(!value||typeof value!=='object'||!('csrfToken' in value)||
          typeof value.csrfToken!=='string')throw Error('CSRF');
      const res=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken},
        body});
      if(currentScope!==scope)return;
      if([401,403,404].includes(res.status)){
        setStatus('denied');setSnapshot(null);setPending(null);return;
      }
      if(res.status>=500)throw Error('unknown');
      if(!res.ok){
        setPending(null);
        setMessage(res.status===409?'Конфликт: осмотр уже зафиксирован, QC завершён или ключ занят.':
          'Операция отклонена. Проверьте поля и роль сотрудника.');
        setRevision(n=>n+1);return;
      }
      setPending(null);setRevision(n=>n+1);
      if(key==='qc'){
        queueSequence.current+=1;
        setQueue(emptyQueue());
        void loadQueue(null,true);
      }
    }catch{
      if(currentScope===scope){
        setPending({path,body,key});
        setMessage('Результат неизвестен. Повторите ТОТ ЖЕ запрос или обновите журнал перед новой операцией.');
      }
    }finally{setBusy(false);}
  }
  const odometerValue=Number(odometer),fuelValue=Number(fuel);
  const validInspection=odometer.trim()!==''&&Number.isInteger(odometerValue)&&
    odometerValue>=0&&odometerValue<=2000000&&fuel.trim()!==''&&
    Number.isInteger(fuelValue)&&fuelValue>=0&&fuelValue<=100;
  const canMutate=ready&&!busy&&!pending;
  React.useEffect(()=>{
    queueSequence.current+=1;
    setQueue(emptyQueue());
  },[org,loc]);
  async function loadQueue(after:string|null=null,force=false){
    if(!uuid.test(org.trim())||!uuid.test(loc.trim())||
       (!force&&(busy||queue.status==='loading')))return;
    const seq=++queueSequence.current;
    const branch={...currentBranch.current};
    setQueue({...emptyQueue(),status:'loading'});
    const params=new URLSearchParams({limit:'20'});
    if(after)params.set('afterOrderId',after);
    try {
      const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(branch.org)+
        '/locations/'+encodeURIComponent(branch.loc)+'/inspection-qc/pending?'+params;
      const res=await fetch(path,{credentials:'include',cache:'no-store'});
      if(seq!==queueSequence.current||currentBranch.current.org!==branch.org||
        currentBranch.current.loc!==branch.loc)return;
      if([401,403,404].includes(res.status)){
        setQueue({...emptyQueue(),status:'denied'});setOrder('');
        return;
      }
      if(!res.ok)throw Error('queue');
      const raw:unknown=await res.json();
      if(!raw||typeof raw!=='object')throw Error('format');
      const data=raw as {syntheticOnly?:unknown;items?:unknown;nextOrderId?:unknown};
      if(data.syntheticOnly!==true||!Array.isArray(data.items)||
        data.items.length>20||
        !data.items.every((item:unknown)=>{
          if(!item||typeof item!=='object')return false;
          const row=item as Record<string,unknown>;
          return typeof row.orderId==='string'&&uuid.test(row.orderId)&&
            (row.status==='PENDING_QC'||row.status==='FAIL')&&
            typeof row.lastFindingCode==='string'&&
            (row.status==='PENDING_QC'?row.lastFindingCode==='NONE':
              ['REWORK','DEFECT'].includes(row.lastFindingCode));
        })||!(data.nextOrderId===null||
          (typeof data.nextOrderId==='string'&&uuid.test(data.nextOrderId))))
        throw Error('invalid queue payload');
      setQueue({status:'ready',items:data.items as QueueItem[],
        nextOrderId:data.nextOrderId as string|null,cursor:after});
    }catch{
      if(seq===queueSequence.current&&currentBranch.current.org===branch.org&&
        currentBranch.current.loc===branch.loc)
        setQueue({...emptyQueue(),status:'error'});
    }
  }
  return <section aria-label="Осмотр и контроль качества автомобиля (dev)"
    style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Приёмка и контроль качества (dev)</h1>
    <p>Только синтетический HTTPS-стенд. Осмотр и QC не подтверждают согласование
       сметы, оплату или разрешение на выдачу автомобиля.</p>
    <label>UUID организации <input aria-label="UUID организации" disabled={busy}
      value={org} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" disabled={busy}
      value={loc} onChange={e=>setLoc(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" disabled={busy}
      value={order} onChange={e=>setOrder(e.target.value)}/></label>
    <section aria-label="Очередь нарядов на независимый QC">
      <h2>Наряды к проверке качества</h2>
      <p>Только MECHANIC действующего филиала: приёмочный осмотр завершён,
        независимый QC ожидается либо требуется повтор после FAIL.
        Наряды с незавершёнными работами или вашим собственным осмотром/работой
        не предлагаются. Список не подтверждает разрешение на выдачу.</p>
      <button type="button" disabled={busy||queue.status==='loading'||
        !uuid.test(org.trim())||!uuid.test(loc.trim())}
        onClick={()=>void loadQueue()}>Обновить очередь QC</button>
      {queue.status==='loading'&&<p role="status">Загрузка очереди QC…</p>}
      {queue.status==='denied'&&<p role="alert">Нет доступа к очереди QC филиала.</p>}
      {queue.status==='error'&&<p role="alert">Не удалось загрузить очередь QC.</p>}
      {queue.status==='ready'&&(queue.items.length?
        <ul>{queue.items.map(item=><li key={item.orderId}>
          <code>{item.orderId}</code> · {item.status==='FAIL'?
            'Требуется повторный QC: '+item.lastFindingCode:'Ожидает первого QC'}
          <button type="button" disabled={busy||!!pending}
            onClick={()=>setOrder(item.orderId)}>
            Открыть QC {item.orderId}</button>
        </li>)}</ul>:<p>Нарядов в очереди QC нет.</p>)}
      {queue.status==='ready'&&queue.nextOrderId&&
        <button type="button" disabled={busy}
          onClick={()=>void loadQueue(queue.nextOrderId)}>Следующие наряды QC</button>}
      {queue.status==='ready'&&queue.cursor&&
        <button type="button" disabled={busy}
          onClick={()=>void loadQueue()}>Первая страница QC</button>}
    </section>
    {status==='idle'&&<p>Укажите три UUID для проверки действующего заказ-наряда.</p>}
    {status==='loading'&&<p role="status">Загрузка осмотра и журнала QC…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён, запись отменена
      или связь с автомобилем отозвана.</p>}
    {status==='error'&&<><p role="alert">Не удалось загрузить журнал.</p>
      <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить загрузку</button></>}
    {ready&&snapshot&&<>
      <h2>Состояние: {snapshot.status}</h2>
      <p>Выдача не разрешена этим dev-модулем.</p>
      {snapshot.inspection?<section aria-label="Зафиксированный осмотр">
        <h3>Приёмочный осмотр</h3>
        <p>Пробег: {snapshot.inspection.odometerKm} км · Топливо:
          {' '}{snapshot.inspection.fuelPercent}% · Кузов: {snapshot.inspection.exteriorCode}</p>
        <p>Шины: {snapshot.inspection.tyresOk?'OK':'требуют проверки'} · Свет:
          {' '}{snapshot.inspection.lightsOk?'OK':'требует проверки'} · Утечка:
          {' '}{snapshot.inspection.fluidLeak?'да':'нет'}</p>
      </section>:<form onSubmit={e=>{
        e.preventDefault();if(!canMutate||!validInspection)return;
        const body=JSON.stringify({odometerKm:odometerValue,fuelPercent:fuelValue,
          tyresOk:tyres,lightsOk:lights,fluidLeak:leak,exteriorCode:exterior});
        void execute(root+'/inspection',body,'inspection');
      }}>
        <h3>Приёмочный осмотр (INTAKE)</h3>
        <label>Пробег, км <input aria-label="Пробег, км" type="number" min="0" max="2000000"
          value={odometer} onChange={e=>setOdometer(e.target.value)}/></label>
        <label>Топливо, % <input aria-label="Топливо, %" type="number" min="0" max="100"
          value={fuel} onChange={e=>setFuel(e.target.value)}/></label>
        <label><input type="checkbox" checked={tyres} onChange={e=>setTyres(e.target.checked)}/> Шины проверены</label>
        <label><input type="checkbox" checked={lights} onChange={e=>setLights(e.target.checked)}/> Освещение проверено</label>
        <label><input type="checkbox" checked={leak} onChange={e=>setLeak(e.target.checked)}/> Обнаружена утечка</label>
        <label>Кузов <select aria-label="Состояние кузова" value={exterior}
          onChange={e=>setExterior(e.target.value)}>
          <option value="UNCHECKED">Не проверен</option><option value="CLEAR">Без видимых повреждений</option>
          <option value="DAMAGE">Есть повреждения</option></select></label>
        <button type="submit" disabled={!canMutate||!validInspection}>Зафиксировать осмотр</button>
      </form>}
      <h3>Журнал QC</h3>
      {snapshot.qcEvents.length===0?<p>Проверок качества пока нет.</p>:
        <ol>{snapshot.qcEvents.map(event=><li key={event.id}>
          {event.result} · {event.findingCode} · {event.createdAt}
        </li>)}</ol>}
      {snapshot.inspection&&snapshot.status!=='QC_PASSED'&&<form onSubmit={e=>{
        e.preventDefault();if(!canMutate)return;
        const body=JSON.stringify({requestKey:crypto.randomUUID(),result:qcResult,
          findingCode:qcResult==='PASS'?'NONE':finding});
        void execute(root+'/qc',body,'qc');
      }}>
        <h3>Независимый контроль качества (MECHANIC)</h3>
        <label>Результат QC <select aria-label="Результат QC" value={qcResult}
          onChange={e=>setQcResult(e.target.value as 'PASS'|'FAIL')}>
          <option value="FAIL">FAIL — вернуть на доработку</option>
          <option value="PASS">PASS — проверка пройдена</option></select></label>
        {qcResult==='FAIL'&&<label>Причина отказа <select aria-label="Причина отказа"
          value={finding} onChange={e=>setFinding(e.target.value)}>
          <option value="REWORK">Требуется доработка</option>
          <option value="DEFECT">Выявлен дефект</option></select></label>}
        <button type="submit" disabled={!canMutate}>Записать результат QC</button>
      </form>}
      <button type="button" disabled={busy}
        onClick={()=>setRevision(n=>n+1)}>Обновить журнал</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&ready&&<div role="group" aria-label="Неизвестный результат операции">
      <button type="button" disabled={busy} onClick={()=>void execute(pending.path,pending.body,pending.key)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setRevision(n=>n+1);}}>
        Проверить журнал перед новой операцией</button>
    </div>}
  </section>;
}
