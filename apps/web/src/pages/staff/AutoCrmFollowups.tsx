import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const kinds=['CALLBACK_NOT_SENT','CHECK_DOCUMENTS','CONFIRM_BOOKING',
  'SERVICE_REVIEW_NOT_SENT'] as const;
const reasons=['NO_LONGER_NEEDED','DUPLICATE','OTHER'] as const;
type Task={id:string;ownerId:string;createdById:string;kind:string;dueDay:string;
  createdAt:string;status:'OPEN'|'DONE'|'CANCELED';reasonCode:string|null;
  closedById:string|null;closedAt:string|null};
type Pending={url:string;body:string;scope:string};
const day=/^\d{4}-\d{2}-\d{2}$/;
function validTask(x:unknown):x is Task{
  if(!x||typeof x!=='object')return false;
  const t=x as Task;
  return [t.id,t.ownerId,t.createdById].every(v=>typeof v==='string'&&uuid.test(v))&&
    kinds.some(v=>v===t.kind)&&typeof t.dueDay==='string'&&day.test(t.dueDay)&&
    typeof t.createdAt==='string'&&['OPEN','DONE','CANCELED'].includes(t.status)&&
    (t.reasonCode===null||reasons.some(v=>v===t.reasonCode))&&
    (t.closedById===null||typeof t.closedById==='string'&&uuid.test(t.closedById))&&
    (t.closedAt===null||typeof t.closedAt==='string')&&
    (t.status==='OPEN'?t.closedAt===null&&t.reasonCode===null&&t.closedById===null:
      t.closedAt!==null&&t.closedById!==null&&
      (t.status==='DONE'?t.reasonCode===null:t.reasonCode!==null));
}
function validList(v:unknown):Task[]{
  if(!v||typeof v!=='object'||!('syntheticOnly' in v)||
    v.syntheticOnly!==true||!('items' in v)||!Array.isArray(v.items)||
    v.items.length>50||!v.items.every(validTask))throw Error('INVALID_RESPONSE');
  return v.items as Task[];
}
export function AutoCrmFollowups({org,loc,lead}:{org:string;loc:string;lead:string}){
  const [items,setItems]=React.useState<Task[]>([]);
  const [kind,setKind]=React.useState<(typeof kinds)[number]>('CALLBACK_NOT_SENT');
  const [due,setDue]=React.useState('');
  const [reasonsByTask,setReasonsByTask]=React.useState<Record<string,(typeof reasons)[number]>>({});
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [revision,setRevision]=React.useState(0);
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const scope=[org,loc,lead].join(':');
  const currentScope=React.useRef(scope);currentScope.current=scope;
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/crm/leads/'+encodeURIComponent(lead)+'/followups';
  React.useEffect(()=>{
    setItems([]);setMessage('');setPending(null);setStatus('loading');
    const abort=new AbortController();
    void fetch(base,{credentials:'include',cache:'no-store',signal:abort.signal})
      .then(async res=>{
        if(abort.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('FETCH');
        const data=validList(await res.json());
        if(!abort.signal.aborted){setItems(data);setStatus('ready');}
      }).catch(()=>{if(!abort.signal.aborted)setStatus('error');});
    return ()=>abort.abort();
  },[scope,revision]);
  async function mutate(url:string,body:string,retry=false){
    if(busy||status!=='ready'||(!retry&&pending))return;
    const originalScope=scope;setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if(!csrf.ok)throw Error('CSRF');
      const data:unknown=await csrf.json();
      if(!data||typeof data!=='object'||!('csrfToken' in data)||
        typeof data.csrfToken!=='string')throw Error('CSRF');
      const result=await fetch(url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrfToken},
        body});
      if(currentScope.current!==originalScope)return;
      if([401,403,404].includes(result.status)){
        setItems([]);setPending(null);setStatus('denied');return;
      }
      if(result.status===409||result.status===422){
        setPending(null);
        setMessage(result.status===409?'Конфликт (409): задача уже существует, закрыта или ключ занят.':
          'Некорректный срок, вид задачи или причина (422).');
        setRevision(x=>x+1);return;
      }
      if(!result.ok)throw Error('UNKNOWN_RESULT');
      const response:unknown=await result.json();
      if(!response||typeof response!=='object'||!('syntheticOnly' in response)||
        response.syntheticOnly!==true||!('task' in response)||
        !validTask(response.task)||!('replayed' in response)||
        typeof response.replayed!=='boolean')throw Error('BAD_RECEIPT');
      if(currentScope.current!==originalScope)return;
      const task=response.task;
      setItems(old=>old.some(x=>x.id===task.id)?
        old.map(x=>x.id===task.id?task:x):[...old,task].sort(
          (a,b)=>a.dueDay.localeCompare(b.dueDay)).slice(0,50));
      setPending(null);
      setMessage(response.replayed?'Точный повтор подтверждён.':'Событие зафиксировано.');
    }catch{
      if(currentScope.current===originalScope){
        setPending({url,body,scope:originalScope});
        setMessage('Результат операции неизвестен. Повторите с тем же requestKey или обновите журнал.');
      }
    }finally{setBusy(false);}
  }
  function request(url:string,body:Record<string,unknown>){
    void mutate(url,JSON.stringify({requestKey:crypto.randomUUID(),...body}));
  }
  return <section aria-label="Задачи по CRM-обращению (dev)">
    <h3>Задачи обратной связи (без отправки сообщений)</h3>
    <p>Сроки и журнал ручных действий. Никакие звонки, SMS или email
      не запускаются; задачи не доказывают наличие согласия на контакт.</p>
    {status==='loading'&&<p role="status">Загрузка задач…</p>}
    {status==='denied'&&<p role="alert">Доступ к задачам запрещён или подтверждение клиента отозвано.</p>}
    {status==='error'&&<p role="alert">Не удалось загрузить задачи.</p>}
    {status==='ready'&&<>
      <label>Вид задачи <select aria-label="Вид задачи" value={kind}
        onChange={e=>setKind(e.target.value as typeof kind)}>
        {kinds.map(v=><option key={v} value={v}>{v}</option>)}
      </select></label>
      <label>Срок задачи <input aria-label="Срок задачи" type="date" value={due}
        onChange={e=>setDue(e.target.value)}/></label>
      <button type="button" disabled={busy||!!pending||!day.test(due)}
        onClick={()=>request(base,{kind,dueDay:due})}>Добавить задачу</button>
      {items.length===0?<p>Задач нет.</p>:
        <div style={{overflowX:'auto'}}><table>
          <caption>Задачи и неизменяемые результаты по обращению</caption>
          <thead><tr><th scope="col">Вид</th><th scope="col">Срок</th>
            <th scope="col">Ответственный</th><th scope="col">Состояние</th>
            <th scope="col">Завершение</th></tr></thead>
          <tbody>{items.map(task=><tr key={task.id}>
            <th scope="row">{task.kind}</th><td>{task.dueDay}</td>
            <td>{task.ownerId}</td><td>{task.status}</td>
            <td>{task.status==='OPEN'?<>
              <button type="button" disabled={busy||!!pending}
                onClick={()=>request(base+'/'+task.id+'/finish',{
                  action:'DONE',reasonCode:null,
                })}>Выполнено</button>
              <label>Причина отмены <select aria-label={'Причина отмены '+task.id}
                value={reasonsByTask[task.id]??'NO_LONGER_NEEDED'}
                onChange={e=>setReasonsByTask(v=>({...v,
                  [task.id]:e.target.value as typeof reasons[number]}))}>
                {reasons.map(v=><option key={v} value={v}>{v}</option>)}
              </select></label>
              <button type="button" disabled={busy||!!pending}
                onClick={()=>request(base+'/'+task.id+'/finish',{
                  action:'CANCELED',reasonCode:reasonsByTask[task.id]??'NO_LONGER_NEEDED',
                })}>Отменить задачу</button>
            </>:task.reasonCode??'Зафиксировано'}</td>
          </tr>)}</tbody>
        </table></div>}
      {message&&<p role="alert">{message}</p>}
      {pending&&<button type="button" disabled={busy||pending.scope!==scope}
        onClick={()=>void mutate(pending.url,pending.body,true)}>
        Повторить тот же запрос</button>}
      <button type="button" disabled={busy}
        onClick={()=>setRevision(x=>x+1)}>Обновить задачи</button>
    </>}
  </section>;
}
