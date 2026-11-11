import React from 'react';
import { Link } from 'react-router-dom';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const offsetTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-](?:0\d|1\d|2[0-3]):[0-5]\d)$/;
const inputStyle:React.CSSProperties = {width:'min(100%, 28rem)',maxWidth:'100%',boxSizing:'border-box'};
const isStamp = (v:unknown):v is string => typeof v==='string' &&
  Number.isFinite(Date.parse(v)) && /^\d{4}-\d\d-\d\dT/.test(v);
type Assignment = {
  assignmentId:string;workOrderId:string;mechanicId:string;
  startsAt:string;endsAt:string;status:'OFFERED'|'ACCEPTED';
  acceptedAt:string|null;canRespond:boolean;
};
type Queue = {
  syntheticOnly:true;releaseAllowed:false;canOffer:boolean;items:Assignment[];
};
type Pending = {path:string;body:string;label:string};

function parseQueue(raw:unknown):Queue {
  if(!raw||typeof raw!=='object')throw Error('contract');
  const r=raw as Record<string,unknown>;
  if(r.syntheticOnly!==true||r.releaseAllowed!==false||
    typeof r.canOffer!=='boolean'||!Array.isArray(r.items)||r.items.length>100)
    throw Error('contract');
  const ids=new Set<string>();
  for(const x of r.items){
    if(!x||typeof x!=='object')throw Error('contract');
    const a=x as Record<string,unknown>;
    if(![a.assignmentId,a.workOrderId,a.mechanicId].every(v=>typeof v==='string'&&uuid.test(v))||
       !isStamp(a.startsAt)||!isStamp(a.endsAt)||
       !['OFFERED','ACCEPTED'].includes(String(a.status))||
       typeof a.canRespond!=='boolean'||
       (a.acceptedAt!==null&&!isStamp(a.acceptedAt))||
       (a.status==='ACCEPTED'&&a.acceptedAt===null)||
       (a.status==='ACCEPTED'&&a.canRespond)||
       ids.has(String(a.assignmentId)))throw Error('contract');
    ids.add(String(a.assignmentId));
  }
  return raw as Queue;
}
function parseOfferWindow(a:string,b:string){
  if(!offsetTime.test(a)||!offsetTime.test(b))return null;
  const start=Date.parse(a),end=Date.parse(b);
  if(!Number.isFinite(start)||!Number.isFinite(end)||
     start<=Date.now()||end<=start||end-start>24*60*60*1000)return null;
  return {start:new Date(start).toISOString(),end:new Date(end).toISOString()};
}
function formatDate(stamp:string){
  return new Date(stamp).toLocaleString('ru-RU',{dateStyle:'short',timeStyle:'short'});
}

export function AutoAssignments(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [mechanic,setMechanic]=React.useState('');
  const [starts,setStarts]=React.useState('');
  const [ends,setEnds]=React.useState('');
  const [revision,setRevision]=React.useState(0);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'|'limit'>('idle');
  const [loadedScope,setLoadedScope]=React.useState('');
  const [queue,setQueue]=React.useState<Queue|null>(null);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const active=React.useRef<AbortController|null>(null);
  const mutation=React.useRef<AbortController|null>(null);
  const scope=org.trim()+':'+loc.trim();
  const valid=uuid.test(org.trim())&&uuid.test(loc.trim());
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim());
  const ready=status==='ready'&&loadedScope===scope&&queue!==null;
  const offerWindow=parseOfferWindow(starts,ends);
  const canOffer=ready&&queue.canOffer&&uuid.test(order.trim())&&
    uuid.test(mechanic.trim())&&offerWindow!==null&&pending===null&&!busy;
  const canMutate=ready&&pending===null&&!busy;

  React.useEffect(()=>()=>{
    active.current?.abort();mutation.current?.abort();
  },[]);

  function scopeChanged(setter:(v:string)=>void,v:string){
    active.current?.abort();mutation.current?.abort();
    setter(v);setQueue(null);setLoadedScope('');
    setStatus('idle');setMessage('');setPending(null);setBusy(false);
  }

  React.useEffect(()=>{
    active.current?.abort();
    setQueue(null);setLoadedScope('');
    if(!valid){setStatus('idle');return;}
    const controller=new AbortController();active.current=controller;
    setStatus('loading');
    (async()=>{
      try{
        const response=await fetch(root+'/assignments',{
          credentials:'include',cache:'no-store',signal:controller.signal,
        });
        if(controller.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(response.status===409){setStatus('limit');return;}
        if(!response.ok)throw Error('http');
        const data=parseQueue(await response.json());
        if(!controller.signal.aborted){
          setQueue(data);setLoadedScope(scope);setStatus('ready');
        }
      }catch{if(!controller.signal.aborted)setStatus('error');}
    })();
    return ()=>controller.abort();
  // The base URL is recomputed from the same two scope fields as these dependencies.
  },[org,loc,revision,valid]);

  function offer(){
    if(!canOffer||!offerWindow)return;
    void send({
      path:root+'/orders/'+encodeURIComponent(order.trim())+'/assignments',
      body:JSON.stringify({mechanicId:mechanic.trim(),
        startsAt:offerWindow.start,endsAt:offerWindow.end,
        requestKey:crypto.randomUUID()}),
      label:'Предложение назначения',
    });
  }
  function respond(a:Assignment,action:'accept'|'decline'){
    if(!canMutate||!a.canRespond||a.status!=='OFFERED')return;
    void send({
      path:root+'/assignments/'+encodeURIComponent(a.assignmentId)+'/'+action,
      body:JSON.stringify({requestKey:crypto.randomUUID()}),
      label:action==='accept'?'Принятие назначения':'Отклонение назначения',
    });
  }
  async function send(task:Pending){
    if(!valid||busy||(pending!==null&&pending!==task))return;
    const controller=new AbortController();mutation.current=controller;
    setPending(task);setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',signal:controller.signal,
      });
      if(!csrf.ok)throw Error('csrf');
      const credential:unknown=await csrf.json();
      if(!credential||typeof credential!=='object'||!('csrfToken' in credential)||
        typeof credential.csrfToken!=='string'||credential.csrfToken.length<1)
        throw Error('csrf');
      const res=await fetch(task.path,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':credential.csrfToken},
        body:task.body,signal:controller.signal,
      });
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){
        setQueue(null);setStatus('denied');setPending(null);
        setMessage('Доступ или актуальность записи отозваны. Проверьте филиал, смену и права.');
        return;
      }
      if(res.status===409){
        setPending(null);setMessage('409 — конфликт состояния или занятого интервала. Обновите очередь.');
        return;
      }
      if(res.status===422){
        setPending(null);setMessage('422 — сервер отклонил данные. Проверьте идентификаторы и интервал.');
        return;
      }
      if(res.status!==200&&res.status!==201)throw Error('http');
      const payload:unknown=await res.json();
      if(!payload||typeof payload!=='object'||!('ok' in payload)||
        payload.ok!==true||!('replayed' in payload)||
        typeof payload.replayed!=='boolean')throw Error('contract');
      if(!controller.signal.aborted){
        setPending(null);setMessage(task.label+' зафиксировано. Очередь обновлена.');
        setRevision(value=>value+1);
      }
    }catch{
      if(!controller.signal.aborted){
        // Request may already have committed. Never invent a second key.
        setQueue(null);setStatus('error');
        setMessage('Результат неизвестен. Повторите тот же запрос с исходным ключом.');
      }
    }finally{
      if(!controller.signal.aborted)setBusy(false);
    }
  }

  return <section aria-label="Назначения механиков (dev)"
      style={{maxWidth:'80rem',margin:'1rem auto',overflowWrap:'anywhere'}}>
    <h1>Назначения механиков</h1>
    <p><strong>Только synthetic dev.</strong> Руководитель предлагает работу
      в рамках действующей смены, механик принимает или отклоняет
      <strong> только своё</strong> предложение. Текущие записи фильтруются
      сервером по роли, филиалу, проверенному автомобилю, текущей брони
      и активной смене. Не является разрешением на ремонт, QC или выдачу.</p>
    <div style={{display:'flex',flexWrap:'wrap',gap:'1rem',alignItems:'end'}}>
      <label>UUID организации <input aria-label="UUID организации" style={inputStyle}
        value={org} disabled={busy}
        onChange={e=>scopeChanged(setOrg,e.target.value)}/></label>
      <label>UUID филиала <input aria-label="UUID филиала" style={inputStyle}
        value={loc} disabled={busy}
        onChange={e=>scopeChanged(setLoc,e.target.value)}/></label>
      <button type="button" disabled={!valid||busy} onClick={()=>setRevision(n=>n+1)}>
        Обновить назначения</button>
    </div>
    {status==='idle'&&<p>Укажите UUID своей организации и филиала.</p>}
    {status==='loading'&&<p role="status">Проверяем действующие назначения…</p>}
    {status==='denied'&&<p role="alert">403 — доступ запрещён или связь с филиалом отозвана.</p>}
    {status==='limit'&&<p role="alert">409 — более 100 действующих назначений. Требуется администратор.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить актуальную очередь.</p>}
    {message&&<p role="status">{message}</p>}
    {pending&&<button type="button" disabled={busy||!valid}
      onClick={()=>void send(pending)}>Повторить тот же запрос</button>}
    {ready&&queue.canOffer&&<fieldset style={{marginTop:'1rem',maxWidth:'100%'}}>
      <legend>Предложить работу механику (MANAGER)</legend>
      <p>Укажите будущий интервал с явным часовым смещением, например
        <code>2030-01-20T09:00:00+03:00</code>.
        Он должен полностью входить в действующую смену механика
        и продолжаться не более 24 часов. Время показывается в локальной зоне браузера.</p>
      <div style={{display:'flex',flexWrap:'wrap',gap:'1rem'}}>
        <label>UUID заказ-наряда <input style={inputStyle}
          aria-label="UUID заказ-наряда" value={order}
          disabled={pending!==null||busy}
          onChange={e=>setOrder(e.target.value)}/></label>
        <label>UUID механика <input style={inputStyle}
          aria-label="UUID механика" value={mechanic}
          disabled={pending!==null||busy}
          onChange={e=>setMechanic(e.target.value)}/></label>
        <label>Начало с часовым поясом <input style={inputStyle}
          aria-label="Начало назначения (ISO с поясом)" value={starts}
          placeholder="2030-01-20T09:00:00+03:00"
          disabled={pending!==null||busy}
          onChange={e=>setStarts(e.target.value)}/></label>
        <label>Конец с часовым поясом <input style={inputStyle}
          aria-label="Конец назначения (ISO с поясом)" value={ends}
          placeholder="2030-01-20T11:00:00+03:00"
          disabled={pending!==null||busy}
          onChange={e=>setEnds(e.target.value)}/></label>
      </div>
      <button type="button" disabled={!canOffer} onClick={offer}>
        Предложить назначение</button>
      {(starts!==''||ends!=='')&&offerWindow===null&&
        <p>Нужны будущие ISO-даты со смещением и интервал не более 24 часов.</p>}
    </fieldset>}
    {ready&&<section aria-label="Текущая очередь назначений">
      <h2>Действующие предложения и принятые задания</h2>
      <p>В списке нет отменённых, неподтверждённых и потерявших действующую
        смену назначений. Список — только подсказка: ответ проверяется заново.</p>
      {queue.items.length===0
        ? <p>Для ваших прав действующих назначений нет.</p>
        : <div style={{maxWidth:'100%',overflowX:'auto'}}>
          <table>
            <caption>Назначения по времени начала</caption>
            <thead><tr><th scope="col">Заказ-наряд</th>
              <th scope="col">Механик</th><th scope="col">Интервал</th>
              <th scope="col">Статус</th><th scope="col">Действия</th></tr></thead>
            <tbody>{queue.items.map(a=><tr key={a.assignmentId}>
              <th scope="row">{a.workOrderId}</th>
              <td>{a.mechanicId}</td>
              <td>{formatDate(a.startsAt)} — {formatDate(a.endsAt)}</td>
              <td>{a.status==='OFFERED'?'Ожидает ответа':'Принято'}</td>
              <td>
                {a.canRespond&&a.status==='OFFERED'&&<>
                  <button type="button" disabled={!canMutate}
                    onClick={()=>respond(a,'accept')}>Принять назначение</button>{' '}
                  <button type="button" disabled={!canMutate}
                    onClick={()=>respond(a,'decline')}>Отклонить назначение</button>
                </>}
                <Link to={'/staff/auto-execution?'+new URLSearchParams({
                  org:org.trim(),loc:loc.trim(),order:a.workOrderId,
                }).toString()}>Перейти к работам</Link>
              </td>
            </tr>)}</tbody>
          </table>
        </div>}
    </section>}
  </section>;
}
