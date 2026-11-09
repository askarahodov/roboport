import React from 'react';
import { Link } from 'react-router-dom';

type Service={serviceId:string;code:string;title:string;durationMinutes:number};
type Labor={serviceId:string;actualMinutes:number;createdAt:string};
type Material={issueId:string;lotId:string;skuId:string;quantity:number;received:boolean};
type Snapshot={status:'NOT_APPROVED'|'APPROVED'|'IN_PROGRESS'|'READY_FOR_QC'|'QC_ACCEPTED';
  approvedVersion:number|null;approvedTotalMinor:number|null;requiredServices:Service[];
  labor:Labor[];materials:Material[];qcStatus:string;executionId:string|null;
  syntheticOnly:true;releaseAllowed:false};
type Assignment={assignmentId:string;workOrderId:string;mechanicId:string;startsAt:string;endsAt:string;status:'OFFERED'|'ACCEPTED'};
type Pending={path:string;body:string};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function parse(raw:unknown):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('invalid');
  const v=raw as Record<string,unknown>;
  if(!['NOT_APPROVED','APPROVED','IN_PROGRESS','READY_FOR_QC','QC_ACCEPTED'].includes(String(v.status))
    ||v.syntheticOnly!==true||v.releaseAllowed!==false
    ||!Array.isArray(v.requiredServices)||v.requiredServices.length>50
    ||!Array.isArray(v.labor)||v.labor.length>50
    ||!Array.isArray(v.materials)||v.materials.length>100)throw Error('invalid');
  return raw as Snapshot;
}

export function AutoWorkExecution(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState(''),[order,setOrder]=React.useState('');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [loaded,setLoaded]=React.useState(''),[snapshot,setSnapshot]=React.useState<Snapshot|null>(null);
  const [revision,setRevision]=React.useState(0),[busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState(''),[pending,setPending]=React.useState<Pending|null>(null);
  const [minutes,setMinutes]=React.useState<Record<string,string>>({});
  const [lot,setLot]=React.useState(''),[qty,setQty]=React.useState('1');
  const [issue,setIssue]=React.useState('');
  const [assignments,setAssignments]=React.useState<Assignment[]>([]);
  const [mechanicId,setMechanicId]=React.useState('');
  const [shiftStart,setShiftStart]=React.useState('');
  const [shiftEnd,setShiftEnd]=React.useState('');
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const valid=[org,loc,order].every(x=>uuid.test(x.trim()));
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim());
  const ready=status==='ready'&&loaded===scope&&snapshot!==null;
  React.useEffect(()=>{
    setSnapshot(null);setLoaded('');setPending(null);setMessage('');
    setStatus(valid?'loading':'idle');
    if(!valid)return;
    const controller=new AbortController();
    void fetch(root+'/execution',{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async res=>{
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('failed');
        const data=parse(await res.json());
        if(!controller.signal.aborted){setSnapshot(data);setLoaded(scope);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,valid,revision]);

  React.useEffect(()=>{
    setAssignments([]);
    if(!valid)return;
    const controller=new AbortController();
    const queue='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
      '/locations/'+encodeURIComponent(loc.trim())+'/assignments';
    void fetch(queue,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async res=>{
        if(!res.ok)return;
        const data:unknown=await res.json();
        if(!data||typeof data!=='object'||!('items' in data)||!Array.isArray(data.items))return;
        const safe=data.items.filter((v):v is Assignment=>!!v&&typeof v==='object'
          &&'assignmentId' in v&&typeof v.assignmentId==='string'
          &&'workOrderId' in v&&typeof v.workOrderId==='string'
          &&'mechanicId' in v&&typeof v.mechanicId==='string'
          &&'startsAt' in v&&typeof v.startsAt==='string'
          &&'endsAt' in v&&typeof v.endsAt==='string'
          &&'status' in v&&(v.status==='OFFERED'||v.status==='ACCEPTED'));
        if(!controller.signal.aborted)setAssignments(safe);
      }).catch(()=>{});
    return ()=>controller.abort();
  },[org,loc,valid,revision]);

  async function act(path:string,body:string){
    if(!ready||busy)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('csrf');
      const value:unknown=await csrf.json();
      if(!value||typeof value!=='object'||!('csrfToken' in value)
        ||typeof value.csrfToken!=='string')throw Error('csrf');
      const res=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken},body});
      if([401,403,404].includes(res.status)){
        setSnapshot(null);setStatus('denied');setPending(null);return;
      }
      if(res.status===409||res.status===422){
        setPending(null);
        setMessage(res.status===409?'Конфликт: проверьте согласование, выполнение работ и передачу материалов.':
          'Проверьте введённые данные.');
        setRevision(n=>n+1);return;
      }
      if(!res.ok)throw Error('unknown');
      setPending(null);setRevision(n=>n+1);
    }catch{
      setPending({path,body});setMessage('Результат неизвестен. Повторите тот же запрос или перечитайте журнал.');
    }finally{setBusy(false);}
  }
  function send(kind:string,body:Record<string,unknown>){
    void act(root+'/execution/'+kind,JSON.stringify({...body,requestKey:crypto.randomUUID()}));
  }
  const active=ready&&!busy&&!pending;
  const worked=new Set(snapshot?.labor.map(x=>x.serviceId));
  const outstanding=snapshot?.materials.filter(x=>!x.received)??[];
  return <section aria-label="Исполнение автомобильного заказ-наряда (dev)"
    style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Выполнение работ и материалы (dev)</h1>
    <p>Только синтетический HTTPS-стенд. Все действия записываются неизменяемо.
      Ни один этап не разрешает выдачу, оплату или проведение документа.</p>
    <label>UUID организации <input aria-label="UUID организации" disabled={busy}
      value={org} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" disabled={busy}
      value={loc} onChange={e=>setLoc(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" disabled={busy}
      value={order} onChange={e=>setOrder(e.target.value)}/></label>
    {status==='idle'&&<p>Укажите UUID для чтения актуального заказ-наряда.</p>}
    {status==='loading'&&<p role="status">Загрузка статуса исполнения…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён, связь отозвана или запись отменена.</p>}
    {status==='error'&&<><p role="alert">Не удалось загрузить данные.</p>
      <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить загрузку</button></>}
    {ready&&snapshot&&<>
      <h2>Этап: {snapshot.status}</h2>
      <p>Согласованная версия: {snapshot.approvedVersion??'нет'}, сумма:
         {' '}{snapshot.approvedTotalMinor??'нет'} коп.; QC: {snapshot.qcStatus}</p>
      {snapshot.status==='NOT_APPROVED'&&<p>Без подтверждённой клиентом сметы работы запускать нельзя.</p>}
      {snapshot.status==='APPROVED'&&<button type="button" disabled={!active}
        onClick={()=>send('start',{})}>Начать работы (MANAGER)</button>}
      <h3>Назначение исполнителя (MANAGER → MECHANIC)</h3>
      <p>Назначение возможно только в пределах действующей смены. Один механик не может принять пересекающиеся задания.</p>
      <label>UUID механика <input aria-label="UUID механика для назначения" value={mechanicId}
        onChange={e=>setMechanicId(e.target.value)}/></label>
      <label>Начало сменного интервала <input aria-label="Начало назначения" type="datetime-local"
        value={shiftStart} onChange={e=>setShiftStart(e.target.value)}/></label>
      <label>Окончание сменного интервала <input aria-label="Окончание назначения" type="datetime-local"
        value={shiftEnd} onChange={e=>setShiftEnd(e.target.value)}/></label>
      <button type="button" disabled={!active||!uuid.test(mechanicId)
        ||!shiftStart||!shiftEnd||new Date(shiftEnd)<=new Date(shiftStart)}
        onClick={()=>void act(root+'/assignments',JSON.stringify({
          mechanicId,startsAt:new Date(shiftStart).toISOString(),
          endsAt:new Date(shiftEnd).toISOString(),requestKey:crypto.randomUUID()
        }))}>Предложить назначение</button>
      {assignments.filter(a=>a.workOrderId===order.trim()).map(a=><p key={a.assignmentId}>
        Назначение {a.status}; механик {a.mechanicId}; {new Date(a.startsAt).toLocaleString()} — {new Date(a.endsAt).toLocaleString()}
        {a.status==='OFFERED'&&a.mechanicId===mechanicId&&<button type="button" disabled={!active}
          onClick={()=>void act('/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
            '/locations/'+encodeURIComponent(loc.trim())+'/assignments/'+a.assignmentId+'/accept',
            JSON.stringify({requestKey:crypto.randomUUID()}))}>Принять назначение</button>}
      </p>)}
      <h3>Фактические трудозатраты (MECHANIC)</h3>
      {snapshot.requiredServices.length===0?<p>Нет согласованных услуг.</p>:<ul>
        {snapshot.requiredServices.map(s=>{
          const old=snapshot.labor.find(x=>x.serviceId===s.serviceId);
          const value=minutes[s.serviceId]??'';
          const n=Number(value),validMinutes=value.trim()!==''&&
            Number.isInteger(n)&&n>=1&&n<=1440;
          return <li key={s.serviceId}>{s.code} — {s.title} (план {s.durationMinutes} мин.)
            {old?<span> — выполнено {old.actualMinutes} мин.</span>:
              snapshot.status==='IN_PROGRESS'&&<label> Факт, минуты
                <input type="number" aria-label={'Фактические минуты '+s.code}
                  min="1" max="1440" value={value}
                  onChange={e=>setMinutes(prev=>({...prev,[s.serviceId]:e.target.value}))}/>
                <button type="button" disabled={!active||!validMinutes} onClick={()=>
                  send('labor',{serviceId:s.serviceId,actualMinutes:n})}>
                  Зафиксировать работу</button>
              </label>}</li>;
        })}</ul>}
      <h3>Материалы (WAREHOUSE → MECHANIC)</h3>
      {snapshot.materials.length===0?<p>Выдач из партий пока нет.</p>:
        <ul>{snapshot.materials.map(m=><li key={m.issueId}>
          Выдача {m.issueId} · количество {m.quantity} ·
          {' '}{m.received?'получена':'ожидает механика'}
        </li>)}</ul>}
      {snapshot.status==='IN_PROGRESS'&&<>
        <label>UUID партии <input aria-label="UUID партии" value={lot}
          onChange={e=>setLot(e.target.value)}/></label>
        <label>Количество <input aria-label="Количество" type="number" min="1" max="1000000000"
          value={qty} onChange={e=>setQty(e.target.value)}/></label>
        <button type="button" disabled={!active||!uuid.test(lot)||!Number.isInteger(Number(qty))
          ||Number(qty)<1||Number(qty)>1e9} onClick={()=>send('materials/issue',{
          lotId:lot,quantity:Number(qty)})}>Выдать из резерва (WAREHOUSE)</button>
        {outstanding.length>0&&<><label>UUID выдачи для получения
          <select aria-label="Выдача для получения" value={issue}
            onChange={e=>setIssue(e.target.value)}>
            <option value="">Выберите выдачу</option>
            {outstanding.map(m=><option key={m.issueId} value={m.issueId}>{m.issueId}</option>)}
          </select></label>
          <button type="button" disabled={!active||!uuid.test(issue)} onClick={()=>
            send('materials/receive',{issueId:issue})}>Подтвердить получение (MECHANIC)</button></>}
        <button type="button" disabled={!active||snapshot.requiredServices.some(s=>!worked.has(s.serviceId))
          ||outstanding.length>0} onClick={()=>send('finish',{})}>
          Завершить выполнение (MECHANIC)</button>
      </>}
      {(snapshot.status==='READY_FOR_QC'||snapshot.status==='QC_ACCEPTED')&&
        <p>Исполнение завершено. Для независимого контроля откройте
          {' '}<Link to="/staff/auto-workshop-qc">Осмотр и QC (dev)</Link>.
          Выдача автомобиля по-прежнему запрещена.</p>}
      <button type="button" disabled={busy}
        onClick={()=>setRevision(n=>n+1)}>Обновить исполнение</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&ready&&<div role="group" aria-label="Неизвестный результат">
      <button type="button" disabled={busy} onClick={()=>void act(pending.path,pending.body)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy}
        onClick={()=>{setPending(null);setRevision(n=>n+1);}}>Перечитать журнал</button>
    </div>}
  </section>;
}
