import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Location = {locationId:string;code:string;timeZone:string;disabled:boolean;createdAt:string};
type Bay = {bayId:string;locationId:string;code:string;disabled:boolean;createdAt:string};
type Shift = {shiftId:string;locationId:string;userId:string;startsAt:string;endsAt:string;canceled:boolean;createdAt:string};
type Workshop = {syntheticOnly:true;locations:Location[];bays:Bay[];shifts:Shift[];
  locationsTruncated:boolean;baysTruncated:boolean;shiftsTruncated:boolean;
  locationsNextCursor?:string;baysNextCursor?:string;shiftsNextCursor?:string};
type Kind='locations'|'bays'|'shifts';
type Audit = {eventId:string;locationId:string;actorId:string;entityType:'LOCATION'|'BAY'|'SHIFT';
  entityId:string;action:string;createdAt:string};
type State = 'idle'|'loading'|'ready'|'denied'|'error';
type Confirm = {kind:'location'|'bay'|'shift';id:string;locationId:string}|null;
type PendingCreate = {org:string;path:string;body:string;key:string;success:string};

function nextCursor(raw:object, count:number, field='nextCursor'):string|null {
  const value=(raw as Record<string,unknown>)[field];
  if(value===undefined)return null;
  if(typeof value!=='string'||!/^[A-Za-z0-9_-]{1,256}$/.test(value)||count!==100)
    throw Error('cursor');
  return value;
}

function parseWorkshop(raw:unknown):Workshop {
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as Workshop;
  if(x.syntheticOnly!==true||!Array.isArray(x.locations)||!Array.isArray(x.bays)||
    !Array.isArray(x.shifts)||x.locations.length>100||x.bays.length>100||x.shifts.length>100||
    typeof x.locationsTruncated!=='boolean'||typeof x.baysTruncated!=='boolean'||
    typeof x.shiftsTruncated!=='boolean')throw Error('shape');
  if(!x.locations.every(v=>v&&uuid.test(v.locationId)&&typeof v.code==='string'&&
    typeof v.timeZone==='string'&&typeof v.disabled==='boolean'&&typeof v.createdAt==='string')||
    !x.bays.every(v=>v&&uuid.test(v.bayId)&&uuid.test(v.locationId)&&
      typeof v.code==='string'&&typeof v.disabled==='boolean'&&typeof v.createdAt==='string')||
    !x.shifts.every(v=>v&&uuid.test(v.shiftId)&&uuid.test(v.locationId)&&uuid.test(v.userId)&&
      Number.isFinite(Date.parse(v.startsAt))&&Number.isFinite(Date.parse(v.endsAt))&&
      Date.parse(v.startsAt)<Date.parse(v.endsAt)&&typeof v.canceled==='boolean'&&
      typeof v.createdAt==='string'))throw Error('shape');
  for(const kind of ['locations','bays','shifts'] as const){
    if(Boolean(nextCursor(x,x[kind].length,kind+'NextCursor'))!==x[`${kind}Truncated`])
      throw Error('incomplete page');
  }
  return x;
}

function parseAudit(raw:unknown):Audit[] {
  if(!raw||typeof raw!=='object'||!('items' in raw)||!Array.isArray(raw.items)||raw.items.length>100)
    throw Error('audit');
  const rows=raw.items as Audit[];
  if(!rows.every(x=>x&&uuid.test(x.eventId)&&uuid.test(x.locationId)&&uuid.test(x.actorId)&&
    uuid.test(x.entityId)&&['LOCATION','BAY','SHIFT'].includes(x.entityType)&&
    typeof x.action==='string'&&Number.isFinite(Date.parse(x.createdAt))))throw Error('audit');
  return rows;
}

async function post(url:string,body:string|undefined,idempotencyKey:string|undefined,signal:AbortSignal) {
  const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal});
  if([401,403,404].includes(csrf.status))return csrf;
  if(!csrf.ok)throw new Error('Сессия недоступна или истекла');
  const value:unknown=await csrf.json();
  if(!value||typeof value!=='object'||!('csrfToken' in value)||
    typeof value.csrfToken!=='string')throw new Error('Не удалось проверить CSRF');
  return fetch(url,{method:'POST',credentials:'include',cache:'no-store',
    headers:{'X-CSRF-Token':value.csrfToken,'Content-Type':'application/json',
      ...(idempotencyKey?{'Idempotency-Key':idempotencyKey}:{})},
    body,signal});
}

export function AutoOrganizationSetup() {
  const [organization,setOrganization]=React.useState('');
  const [data,setData]=React.useState<Workshop|null>(null);
  const [audit,setAudit]=React.useState<Audit[]>([]);
  const [auditNext,setAuditNext]=React.useState<string|null>(null);
  const sequence=React.useRef(0);
  const [state,setState]=React.useState<State>('idle');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [branchCode,setBranchCode]=React.useState('');
  const [timeZone,setTimeZone]=React.useState('UTC');
  const [locationId,setLocationId]=React.useState('');
  const [bayCode,setBayCode]=React.useState('');
  const [employeeId,setEmployeeId]=React.useState('');
  const [startsAt,setStartsAt]=React.useState('');
  const [endsAt,setEndsAt]=React.useState('');
  const [confirm,setConfirm]=React.useState<Confirm>(null);
  const [pending,setPending]=React.useState<PendingCreate|null>(null);
  const access=React.useRef(new AbortController());
  const orgRef=React.useRef('');
  orgRef.current=organization.trim();
  const validOrg=uuid.test(organization.trim());
  function invalidate(){
    access.current.abort();access.current=new AbortController();sequence.current++;
    setData(null);setAudit([]);setAuditNext(null);setConfirm(null);setLocationId('');
    setPending(null);setBusy(false);setMessage('');
    setBranchCode('');setTimeZone('UTC');setBayCode('');setEmployeeId('');setStartsAt('');setEndsAt('');
  }
  function deny(){invalidate();setState('denied');}
  React.useEffect(()=>{
    if(access.current.signal.aborted)access.current=new AbortController();
    return ()=>access.current.abort();
  },[]);

  async function refresh(org=organization.trim()) {
    const request=++sequence.current,signal=access.current.signal;
    if(!uuid.test(org)){setData(null);setAudit([]);setAuditNext(null);setState('idle');return;}
    setState('loading');setData(null);setAudit([]);setAuditNext(null);setConfirm(null);
    try {
      const [response,history]=await Promise.all([
        fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/workshop',
          {credentials:'include',cache:'no-store',signal}),
        fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/workshop/audit?limit=100',
          {credentials:'include',cache:'no-store',signal}),
      ]);
      if(signal.aborted||orgRef.current!==org||request!==sequence.current)return;
      if([401,403,404].includes(response.status)||[401,403,404].includes(history.status)){
        deny();return;
      }
      if(!response.ok||!history.ok)throw Error('HTTP');
      const workshop=parseWorkshop(await response.json());
      const historyBody:unknown=await history.json();
      const events=parseAudit(historyBody);
      const next=nextCursor(historyBody as object,events.length);
      if(signal.aborted||orgRef.current!==org||request!==sequence.current)return;
      setData(workshop);setAudit(events);setAuditNext(next);
      if(!locationId||!workshop.locations.some(x=>x.locationId===locationId&&!x.disabled)){
        setLocationId(workshop.locations.find(x=>!x.disabled)?.locationId??'');
      }
      setState('ready');
    }catch{
      if(!signal.aborted&&orgRef.current===org&&request===sequence.current)setState('error');
    }
  }

  React.useEffect(()=>{
    invalidate();
    if(uuid.test(organization.trim()))void refresh(organization.trim());
    else setState('idle');
  },[organization]);

  async function more(kind:Kind|'audit',cursor:string) {
    if(busy||pending||!data||state!=='ready')return;
    const org=organization.trim(),request=++sequence.current,signal=access.current.signal;
    setBusy(true);setMessage('');setConfirm(null);
    try{
      const query=new URLSearchParams({limit:'100',cursor});
      if(kind!=='audit')query.set('kind',kind);
      const response=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
        '/workshop/'+(kind==='audit'?'audit':'page')+'?'+query,
        {credentials:'include',cache:'no-store',signal});
      if(signal.aborted||orgRef.current!==org||request!==sequence.current)return;
      if([401,403,404].includes(response.status)){
        deny();return;
      }
      if(!response.ok)throw Error('HTTP');
      const raw:unknown=await response.json();
      if(!raw||typeof raw!=='object'||!('items' in raw)||!Array.isArray(raw.items)||raw.items.length>100)
        throw Error('shape');
      const next=nextCursor(raw,raw.items.length);
      if(next===cursor)throw Error('repeated cursor');
      if(signal.aborted||orgRef.current!==org||request!==sequence.current)return;
      if(kind==='audit'){
        const page=parseAudit(raw);
        const ids=new Set(audit.map(x=>x.eventId));
        if(page.some(x=>ids.has(x.eventId)))throw Error('duplicate');
        setAudit([...audit,...page]);setAuditNext(next);
      }else{
        const page=parseWorkshop({syntheticOnly:true,locations:[],bays:[],shifts:[],
          locationsTruncated:false,baysTruncated:false,shiftsTruncated:false,[kind]:raw.items,
          [kind+'Truncated']:!!next,[kind+'NextCursor']:next??undefined});
        const merged={...data,[kind]:[...data[kind],...page[kind]],
          [kind+'Truncated']:!!next,[kind+'NextCursor']:next??undefined};
        if(kind==='locations'){
          const ids=new Set(data.locations.map(x=>x.locationId));
          if(page.locations.some(x=>ids.has(x.locationId)))throw Error('duplicate');
          if(!locationId)setLocationId(page.locations.find(x=>!x.disabled)?.locationId??'');
        }else if(kind==='bays'){
          const ids=new Set(data.bays.map(x=>x.bayId));
          if(page.bays.some(x=>ids.has(x.bayId)))throw Error('duplicate');
        }else{
          const ids=new Set(data.shifts.map(x=>x.shiftId));
          if(page.shifts.some(x=>ids.has(x.shiftId)))throw Error('duplicate');
        }
        setData(merged);
      }
    }catch{
      if(!signal.aborted&&orgRef.current===org&&request===sequence.current){
        setData(null);setAudit([]);setAuditNext(null);setLocationId('');setState('error');
      }
    }finally{if(!signal.aborted)setBusy(false);}
  }

  async function execute(path:string,payload:unknown,success:string,idempotent=false,retry?:PendingCreate) {
    const operationOrg=organization.trim(),signal=access.current.signal;
    if(busy||state!=='ready'||!uuid.test(operationOrg)||
      (retry?(retry.org!==operationOrg||pending?.key!==retry.key):pending!==null))return;
    // Повтор привязан не только к ключу, но и к исходным пути и JSON.
    const operation=retry??(idempotent?{org:operationOrg,path,body:JSON.stringify(payload),
      key:crypto.randomUUID(),success}:null);
    if(operation)setPending(operation);
    setBusy(true);setMessage('');
    try {
      const response=await post('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(operationOrg)+(operation?.path??path),
        operation?.body??(payload===undefined?undefined:JSON.stringify(payload)),operation?.key,signal);
      if(signal.aborted||orgRef.current!==operationOrg)return;
      if([401,403,404].includes(response.status)){deny();return;}
      if(response.status>=500)throw Error('unknown');
      if(!response.ok){
        setPending(null);
        setMessage(response.status===409?'409 — операция конфликтует с активными записями, сменами или назначениями.'
          :response.status===422?'422 — проверьте код, часовой пояс и даты.'
          :'Операция не выполнена ('+response.status+').');
        return;
      }
      const result:unknown=await response.json();
      if(signal.aborted||orgRef.current!==operationOrg)return;
      if(!result||typeof result!=='object')throw Error('receipt');
      if(operation){
        const field=operation.path.endsWith('/bays')?'bayId':operation.path.endsWith('/shifts')?'shiftId':'locationId';
        const id=(result as Record<string,unknown>)[field];
        if(typeof id!=='string'||!uuid.test(id)||response.status!==201)throw Error('receipt');
      }else if(!('status' in result)||!(path.endsWith('/cancel')?
        ['CANCELED','ALREADY_CANCELED']:['DISABLED','ALREADY_DISABLED']).includes(String(result.status)))throw Error('receipt');
      setPending(null);setMessage(operation?.success??success);setConfirm(null);
      await refresh(operationOrg);
    }catch{
      if(!signal.aborted&&orgRef.current===operationOrg){
        if(operation)setMessage('Результат неизвестен. Повторите исходную операцию с тем же ключом или сначала сверьте структуру.');
        else{
          setData(null);setAudit([]);setAuditNext(null);setConfirm(null);setLocationId('');setState('error');
          setMessage('Результат неизвестен. Обновите структуру перед новым действием.');
        }
      }
    }finally{if(!signal.aborted)setBusy(false);}
  }
  const activeLocations=data?.locations.filter(x=>!x.disabled)??[];
  const locationLabel=(id:string)=>data?.locations.find(x=>x.locationId===id)?.code??id;
  const iso=(value:string,branchId?:string)=>{
    if(!Number.isFinite(Date.parse(value)))return '—';
    const zone=branchId?data?.locations.find(x=>x.locationId===branchId)?.timeZone:undefined;
    const formatted=new Intl.DateTimeFormat('ru-RU',{...(branchId?{timeZone:zone??'UTC'}:{}),
      dateStyle:'short',timeStyle:'short'}).format(new Date(value));
    return branchId?formatted+' ('+(zone??'UTC')+')':formatted;
  };

  return <section className="ui-page crm-workspace" aria-label="Управление организацией и филиалами">
    <div className="crm-heading">
      <div><span className="ui-eyebrow">БОКСРИТМ / АДМИНИСТРИРОВАНИЕ</span>
        <h1>Организация, филиалы и посты</h1>
        <p>Закрытый synthetic-контур. Все изменения требуют полномочия ADMIN конкретной организации.</p>
      </div><span className="status-badge">DEV / TEST</span>
    </div>
    <div className="form-grid">
      <label>UUID организации
        <input aria-label="UUID организации" value={organization} disabled={busy}
          onChange={e=>{if(e.target.value===organization)return;invalidate();setState('idle');setOrganization(e.target.value);}} placeholder="00000000-0000-4000-8000-000000000000"/>
      </label>
      <button type="button" disabled={!validOrg||state==='loading'||busy||pending!==null} onClick={()=>void refresh()}>
        Обновить структуру</button>
    </div>
    {state==='loading'&&<p role="status">Загружаем организацию…</p>}
    {state==='denied'&&<p role="alert">Доступ запрещён. Нужна активная сессия и роль ADMIN этой организации.</p>}
    {state==='error'&&<p role="alert">Не удалось загрузить структуру и журнал аудита.</p>}
    {message&&<p role="status" aria-live="polite">{message}</p>}
    {pending&&<div role="group" aria-label="Неподтверждённая операция структуры">
      <button type="button" disabled={busy||pending!==null} onClick={()=>void execute(pending.path,undefined,pending.success,true,pending)}>Повторить исходную операцию</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setMessage('Сверьте журнал перед новой операцией.');void refresh();}}>Перечитать структуру</button>
    </div>}
    {state==='ready'&&data&&<>
      <section aria-labelledby="branches-title">
        <h2 id="branches-title">Филиалы</h2>
        <p>Часовой пояс задаётся при создании и фиксируется после появления первой смены или брони.</p>
        <form onSubmit={e=>{e.preventDefault();void execute('/locations',{code:branchCode.trim(),timeZone},'Филиал создан.',true);}}>
          <label>Код филиала
            <input value={branchCode} onChange={e=>setBranchCode(e.target.value.toUpperCase())}
              pattern="[A-Z0-9][A-Z0-9_-]{1,31}" minLength={2} maxLength={32} required/>
          </label>
          <label>Часовой пояс IANA
            <input value={timeZone} onChange={e=>setTimeZone(e.target.value)} required/>
          </label>
          <button type="submit" disabled={busy||pending!==null||!branchCode.trim()||!timeZone.trim()}>Создать филиал</button>
        </form>
        <div className="table-scroll" role="region" aria-label="Список филиалов" tabIndex={0}>
          <table><thead><tr><th>Код</th><th>Часовой пояс</th><th>UUID</th><th>Состояние</th><th>Действие</th></tr></thead>
            <tbody>{data.locations.length===0&&<tr><td colSpan={5}>Филиалы ещё не созданы.</td></tr>}{data.locations.map(x=><tr key={x.locationId}>
              <td>{x.code}</td><td>{x.timeZone}</td><td><code>{x.locationId}</code></td>
              <td>{x.disabled?'Отключён':'Активен'}</td>
              <td>{!x.disabled&&(confirm?.kind==='location'&&confirm.id===x.locationId?
                <><button type="button" disabled={busy||pending!==null} onClick={()=>void execute('/locations/'+x.locationId+'/disable',undefined,'Филиал отключён.')}>Подтвердить отключение</button>
                  <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
                <button type="button" disabled={busy||pending!==null} onClick={()=>setConfirm({kind:'location',id:x.locationId,locationId:x.locationId})}>Отключить</button>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {data.locationsNextCursor&&<button type="button" disabled={busy||pending!==null} onClick={()=>void more('locations',data.locationsNextCursor!)}>Показать ещё филиалы</button>}
      </section>

      <section aria-labelledby="bays-title">
        <h2 id="bays-title">Рабочие посты</h2>
        <form onSubmit={e=>{e.preventDefault();void execute('/locations/'+locationId+'/bays',{code:bayCode.trim()},'Рабочий пост создан.',true);}}>
          <label>Филиал
            <select value={locationId} onChange={e=>setLocationId(e.target.value)} required>
              <option value="">Выберите филиал</option>
              {activeLocations.map(x=><option key={x.locationId} value={x.locationId}>{x.code} · {x.timeZone}</option>)}
            </select>
          </label>
          <label>Код поста
            <input value={bayCode} onChange={e=>setBayCode(e.target.value.toUpperCase())}
              pattern="[A-Z0-9][A-Z0-9_-]{1,31}" minLength={2} maxLength={32} required/>
          </label>
          <button type="submit" disabled={busy||pending!==null||!locationId||!bayCode.trim()}>Создать пост</button>
        </form>
        <div className="table-scroll" role="region" aria-label="Список рабочих постов" tabIndex={0}>
          <table><thead><tr><th>Филиал</th><th>Код поста</th><th>UUID</th><th>Состояние</th><th>Действие</th></tr></thead>
            <tbody>{data.bays.length===0&&<tr><td colSpan={5}>Рабочих постов пока нет.</td></tr>}{data.bays.map(x=><tr key={x.bayId}>
              <td>{locationLabel(x.locationId)}</td><td>{x.code}</td><td><code>{x.bayId}</code></td>
              <td>{x.disabled?'Отключён':'Активен'}</td>
              <td>{!x.disabled&&(confirm?.kind==='bay'&&confirm.id===x.bayId?
                <><button type="button" disabled={busy||pending!==null} onClick={()=>void execute('/locations/'+x.locationId+'/bays/'+x.bayId+'/disable',undefined,'Пост отключён.')}>Подтвердить отключение</button>
                  <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
                <button type="button" disabled={busy||pending!==null} onClick={()=>setConfirm({kind:'bay',id:x.bayId,locationId:x.locationId})}>Отключить</button>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {data.baysNextCursor&&<button type="button" disabled={busy||pending!==null} onClick={()=>void more('bays',data.baysNextCursor!)}>Показать ещё посты</button>}
      </section>

      <section aria-labelledby="shifts-title">
        <h2 id="shifts-title">Смены сотрудников</h2>
        <p>Сотрудник должен иметь действующее назначение в этой организации или филиале. Время вводится в ISO 8601 с явным смещением UTC.</p>
        <form onSubmit={e=>{e.preventDefault();void execute('/locations/'+locationId+'/shifts',
          {userId:employeeId.trim(),startsAt:startsAt.trim(),endsAt:endsAt.trim()},'Смена создана.',true);}}>
          <label>Филиал
            <select value={locationId} onChange={e=>setLocationId(e.target.value)} required>
              <option value="">Выберите филиал</option>
              {activeLocations.map(x=><option key={x.locationId} value={x.locationId}>{x.code} · {x.timeZone}</option>)}
            </select>
          </label>
          <label>UUID сотрудника
            <input value={employeeId} onChange={e=>setEmployeeId(e.target.value)} required/>
          </label>
          <label>Начало (ISO 8601 с offset)
            <input value={startsAt} onChange={e=>setStartsAt(e.target.value)}
              placeholder="2030-04-10T09:00:00+02:00" required/>
          </label>
          <label>Окончание (ISO 8601 с offset)
            <input value={endsAt} onChange={e=>setEndsAt(e.target.value)}
              placeholder="2030-04-10T17:00:00+02:00" required/>
          </label>
          <button type="submit" disabled={busy||pending!==null||!locationId||!uuid.test(employeeId.trim())||
            !startsAt.trim()||!endsAt.trim()}>Назначить смену</button>
        </form>
        <div className="table-scroll" role="region" aria-label="Список смен" tabIndex={0}>
          <table><thead><tr><th>Филиал</th><th>Сотрудник UUID</th><th>Начало</th><th>Окончание</th><th>Состояние</th><th>Действие</th></tr></thead>
            <tbody>{data.shifts.length===0&&<tr><td colSpan={6}>Смены ещё не назначены.</td></tr>}{data.shifts.map(x=><tr key={x.shiftId}>
              <td>{locationLabel(x.locationId)}</td><td><code>{x.userId}</code></td>
              <td>{iso(x.startsAt,x.locationId)}</td><td>{iso(x.endsAt,x.locationId)}</td>
              <td>{x.canceled?'Отменена':'Активна'}</td>
              <td>{!x.canceled&&(confirm?.kind==='shift'&&confirm.id===x.shiftId?
                <><button type="button" disabled={busy||pending!==null} onClick={()=>void execute('/locations/'+x.locationId+'/shifts/'+x.shiftId+'/cancel',undefined,'Смена отменена.')}>Подтвердить отмену</button>
                  <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
                <button type="button" disabled={busy||pending!==null} onClick={()=>setConfirm({kind:'shift',id:x.shiftId,locationId:x.locationId})}>Отменить</button>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {data.shiftsNextCursor&&<button type="button" disabled={busy||pending!==null} onClick={()=>void more('shifts',data.shiftsNextCursor!)}>Показать ещё смены</button>}
      </section>

      <section aria-labelledby="workshop-audit-title">
        <h2 id="workshop-audit-title">Журнал административных операций</h2>
        <p>События создания и отключения филиалов/постов и назначения/отмены смен, по 100 записей. Журнал неизменяемый.</p>
        <div className="table-scroll" role="region" aria-label="Журнал административных операций" tabIndex={0}>
          <table><thead><tr><th>Время</th><th>Операция</th><th>Объект</th><th>Актор UUID</th></tr></thead>
            <tbody>{audit.map(x=><tr key={x.eventId}>
              <td>{iso(x.createdAt)}</td><td>{x.action}</td><td>{x.entityType} · <code>{x.entityId}</code></td>
              <td><code>{x.actorId}</code></td>
            </tr>)}</tbody>
          </table>
        </div>
        {auditNext&&<button type="button" disabled={busy||pending!==null} onClick={()=>void more('audit',auditNext)}>Показать ещё события аудита</button>}
      </section>
    </>}
  </section>;
}
