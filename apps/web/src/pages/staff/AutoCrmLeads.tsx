import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sources=['WALK_IN','PHONE_NOT_RECORDED','WEB_FORM_TEST','STAFF_REFERRAL'] as const;
const reasons=['NO_RESPONSE','CUSTOMER_DECLINED','DUPLICATE','OTHER'] as const;
type Stage='NEW'|'CONTACTED'|'BOOKED'|'WON'|'LOST';
type Lead={id:string;ownerId:string;createdById:string;sourceCode:string;
  intakeKey:string;createdAt:string;appointmentId:string|null;stage:Stage;
  reasonCode:string|null;stageChangedAt:string;eventCount:number};
type Pending={url:string;body:string;scope:string};
const stages:Record<Stage,Stage[]>={
  NEW:['CONTACTED','LOST'],CONTACTED:['BOOKED','LOST'],
  BOOKED:['WON','LOST'],WON:[],LOST:[],
};
function validLead(x:unknown):x is Lead{
  if(!x||typeof x!=='object')return false;
  const v=x as Lead;
  return [v.id,v.ownerId,v.createdById,v.intakeKey].every(y=>typeof y==='string'&&uuid.test(y))&&
    sources.some(y=>y===v.sourceCode)&&
    (v.appointmentId===null||typeof v.appointmentId==='string'&&uuid.test(v.appointmentId))&&
    v.stage in stages&&(v.reasonCode===null||reasons.some(y=>y===v.reasonCode))&&
    (v.stage==='LOST'?(v.reasonCode!==null):v.reasonCode===null)&&
    typeof v.createdAt==='string'&&typeof v.stageChangedAt==='string'&&
    Number.isSafeInteger(v.eventCount)&&v.eventCount>=1;
}
function validList(raw:unknown):Lead[]{
  if(!raw||typeof raw!=='object'||!('syntheticOnly' in raw)||
    raw.syntheticOnly!==true||!('items' in raw)||!Array.isArray(raw.items)||
    raw.items.length>50||!raw.items.every(validLead))throw Error('INVALID_RESPONSE');
  return raw.items as Lead[];
}
function newKey():string{return crypto.randomUUID();}

/** Без персональных контактов: только тестовые обращения и привязка к записи. */
export function AutoCrmLeads(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [owner,setOwner]=React.useState('');
  const [source,setSource]=React.useState<(typeof sources)[number]>('WALK_IN');
  const [appointment,setAppointment]=React.useState<Record<string,string>>({});
  const [lossReason,setLossReason]=React.useState<Record<string,(typeof reasons)[number]>>({});
  const [items,setItems]=React.useState<Lead[]>([]);
  const [loadedScope,setLoadedScope]=React.useState('');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [busy,setBusy]=React.useState(false);
  const [revision,setRevision]=React.useState(0);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const controller=React.useRef<AbortController|null>(null);
  const scope=org.trim()+':'+loc.trim();
  const scopeNow=React.useRef(scope);scopeNow.current=scope;
  const ready=uuid.test(org.trim())&&uuid.test(loc.trim());
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/crm-leads';
  React.useEffect(()=>{
    setItems([]);setLoadedScope('');setMessage('');setPending(null);
    if(!ready){setStatus('idle');return;}
    const abort=new AbortController();controller.current=abort;
    setStatus('loading');
    void fetch(base,{credentials:'include',cache:'no-store',signal:abort.signal})
      .then(async response=>{
        if(abort.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw Error('FETCH');
        const list=validList(await response.json());
        if(!abort.signal.aborted){setItems(list);setLoadedScope(scope);setStatus('ready');}
      }).catch(()=>{if(!abort.signal.aborted)setStatus('error');});
    return ()=>abort.abort();
  },[scope,revision,ready]);
  function changeOrg(value:string){controller.current?.abort();setItems([]);setPending(null);setOrg(value);}
  function changeLoc(value:string){controller.current?.abort();setItems([]);setPending(null);setLoc(value);}
  async function mutate(url:string,body:string,isRetry=false){
    if(!ready||busy||status!=='ready'||loadedScope!==scope||(!isRetry&&pending))return;
    const originalScope=scope;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if(!csrf.ok)throw Error('CSRF');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
        typeof token.csrfToken!=='string')throw Error('CSRF');
      const response=await fetch(url,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body,
      });
      if(scopeNow.current!==originalScope)return;
      if([401,403,404].includes(response.status)){
        setItems([]);setPending(null);setStatus('denied');return;
      }
      if(response.status===409||response.status===422){
        setMessage(response.status===409?
          'Конфликт (409): стадия, повторный ключ, связь записи или полномочия изменились. Обновите журнал.':
          'Некорректные параметры (422). Проверьте UUID, источник и стадию.');
        setPending(null);setRevision(n=>n+1);return;
      }
      if(!response.ok)throw Error('UNKNOWN_RESULT');
      setPending(null);setRevision(n=>n+1);
    }catch{
      if(scopeNow.current===originalScope){
        setPending({url,body,scope:originalScope});
        setMessage('Результат неизвестен. Повторите точно тот же запрос с прежним ключом либо обновите журнал.');
      }
    }finally{setBusy(false);}
  }
  const current=status==='ready'&&loadedScope===scope;
  return <section aria-label="CRM обращения автосервиса (dev)" style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>Обращения и стадии CRM (dev)</h1>
    <p>Только synthetic-данные: нет телефонов, email, имени клиента,
      реальной переписки, рассылки или автоматического согласия. Этап
      «Успешно» означает тестовую стадию CRM, а не платёж или выданный автомобиль.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      disabled={busy} onChange={e=>changeOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      disabled={busy} onChange={e=>changeLoc(e.target.value)}/></label>
    {status==='idle'&&<p>Введите UUID организации и филиала.</p>}
    {status==='loading'&&<p role="status">Загрузка обращений…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён. Нужна роль INTAKE или MANAGER действующего филиала.</p>}
    {status==='error'&&<><p role="alert">Не удалось загрузить обращения.</p>
      <button type="button" onClick={()=>setRevision(x=>x+1)}>Повторить загрузку</button></>}
    {current&&<>
      <h2>Создать тестовое обращение</h2>
      <p>Ответственный должен иметь действующую роль приёмщика или руководителя
        этого филиала. Ключ нового обращения создаётся автоматически.</p>
      <label>UUID ответственного <input aria-label="UUID ответственного" value={owner}
        onChange={e=>setOwner(e.target.value)}/></label>
      <label>Источник обращения <select aria-label="Источник обращения"
        value={source} onChange={e=>setSource(e.target.value as typeof source)}>
        {sources.map(v=><option key={v} value={v}>{v}</option>)}
      </select></label>
      <button type="button" disabled={busy||!!pending||!uuid.test(owner)}
        onClick={()=>void mutate(base,JSON.stringify({
          requestKey:newKey(),intakeKey:newKey(),ownerId:owner,sourceCode:source,
        }))}>Зарегистрировать обращение</button>
      <h2>Список обращений (не более 50 последних)</h2>
      {items.length===0?<p>Зарегистрированных обращений пока нет.</p>:
        <div style={{overflowX:'auto'}}><table>
          <caption>Текущие стадии синтетических обращений</caption>
          <thead><tr><th scope="col">Обращение</th><th scope="col">Источник</th>
            <th scope="col">Ответственный</th><th scope="col">Стадия</th>
            <th scope="col">Действия</th></tr></thead>
          <tbody>{items.map(item=><tr key={item.id}>
            <th scope="row">{item.id}</th><td>{item.sourceCode}</td>
            <td>{item.ownerId}</td><td>{item.stage}{item.reasonCode?' / '+item.reasonCode:''}</td>
            <td>
              {item.appointmentId?<span>Запись: {item.appointmentId}</span>:
                <label>UUID записи <input aria-label={'UUID записи '+item.id}
                  value={appointment[item.id]??''}
                  onChange={e=>setAppointment(v=>({...v,[item.id]:e.target.value}))}/>
                  <button type="button" disabled={busy||!!pending||!uuid.test(appointment[item.id]??'')||
                    !['NEW','CONTACTED'].includes(item.stage)}
                    onClick={()=>void mutate(base+'/'+item.id+'/booking',JSON.stringify({
                      requestKey:newKey(),appointmentId:appointment[item.id],
                    }))}>Привязать запись</button>
                </label>}
              {stages[item.stage].map(next=><span key={next}>
                {next==='LOST'&&<label>Причина отказа
                  <select aria-label={'Причина отказа '+item.id}
                    value={lossReason[item.id]??'NO_RESPONSE'}
                    onChange={e=>setLossReason(v=>({...v,[item.id]:e.target.value as typeof reasons[number]}))}>
                    {reasons.map(x=><option key={x} value={x}>{x}</option>)}
                  </select></label>}
                <button type="button" disabled={busy||!!pending||
                  (next==='BOOKED'&&!item.appointmentId)}
                  onClick={()=>void mutate(base+'/'+item.id+'/stage',JSON.stringify({
                    requestKey:newKey(),stage:next,
                    reasonCode:next==='LOST'?(lossReason[item.id]??'NO_RESPONSE'):null,
                  }))}>{next==='CONTACTED'?'Контакт обработан':next==='BOOKED'?'Записан':
                    next==='WON'?'Успешно':'Отказ'}</button>
              </span>)}
            </td>
          </tr>)}</tbody>
        </table></div>}
      {message&&<p role="alert">{message}</p>}
      {pending&&<button type="button" disabled={busy||pending.scope!==scope}
        onClick={()=>void mutate(pending.url,pending.body,true)}>
        Повторить тот же запрос</button>}
      <button type="button" disabled={busy} onClick={()=>setRevision(x=>x+1)}>
        Обновить обращения</button>
    </>}
  </section>;
}
