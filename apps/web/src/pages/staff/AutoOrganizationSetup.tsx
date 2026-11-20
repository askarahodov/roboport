import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Location = {locationId:string;code:string;timeZone:string;disabled:boolean;createdAt:string};
type Bay = {bayId:string;locationId:string;code:string;disabled:boolean;createdAt:string};
type Shift = {shiftId:string;locationId:string;userId:string;startsAt:string;endsAt:string;canceled:boolean;createdAt:string};
type Workshop = {syntheticOnly:true;locations:Location[];bays:Bay[];shifts:Shift[];
  locationsTruncated:boolean;baysTruncated:boolean;shiftsTruncated:boolean};
type Audit = {eventId:string;locationId:string;actorId:string;entityType:'LOCATION'|'BAY'|'SHIFT';
  entityId:string;action:string;createdAt:string};
type State = 'idle'|'loading'|'ready'|'denied'|'error';
type Confirm = {kind:'location'|'bay'|'shift';id:string;locationId:string}|null;

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

async function post(url:string,payload?:unknown) {
  const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
  if(!csrf.ok)throw new Error('Сессия недоступна или истекла');
  const value:unknown=await csrf.json();
  if(!value||typeof value!=='object'||!('csrfToken' in value)||
    typeof value.csrfToken!=='string')throw new Error('Не удалось проверить CSRF');
  return fetch(url,{method:'POST',credentials:'include',cache:'no-store',
    headers:{'X-CSRF-Token':value.csrfToken,'Content-Type':'application/json'},
    body:payload===undefined?undefined:JSON.stringify(payload)});
}

export function AutoOrganizationSetup() {
  const [organization,setOrganization]=React.useState('');
  const [data,setData]=React.useState<Workshop|null>(null);
  const [audit,setAudit]=React.useState<Audit[]>([]);
  const [state,setState]=React.useState<State>('idle');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [branchCode,setBranchCode]=React.useState('');
  const [timeZone,setTimeZone]=React.useState('Europe/Berlin');
  const [locationId,setLocationId]=React.useState('');
  const [bayCode,setBayCode]=React.useState('');
  const [employeeId,setEmployeeId]=React.useState('');
  const [startsAt,setStartsAt]=React.useState('');
  const [endsAt,setEndsAt]=React.useState('');
  const [confirm,setConfirm]=React.useState<Confirm>(null);
  const orgRef=React.useRef('');
  orgRef.current=organization.trim();
  const validOrg=uuid.test(organization.trim());
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(organization.trim());

  async function refresh(org=organization.trim()) {
    if(!uuid.test(org)){setData(null);setAudit([]);setState('idle');return;}
    setState('loading');setData(null);setAudit([]);
    try {
      const [response,history]=await Promise.all([
        fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/workshop',
          {credentials:'include',cache:'no-store'}),
        fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/workshop/audit?limit=100',
          {credentials:'include',cache:'no-store'}),
      ]);
      if(orgRef.current!==org)return;
      if([401,403,404].includes(response.status)||[401,403,404].includes(history.status)){
        setState('denied');return;
      }
      if(!response.ok||!history.ok)throw Error('HTTP');
      const workshop=parseWorkshop(await response.json());
      const events=parseAudit(await history.json());
      if(orgRef.current!==org)return;
      setData(workshop);setAudit(events);
      if(!locationId||!workshop.locations.some(x=>x.locationId===locationId&&!x.disabled)){
        setLocationId(workshop.locations.find(x=>!x.disabled)?.locationId??'');
      }
      setState('ready');
    }catch{
      if(orgRef.current===org)setState('error');
    }
  }

  React.useEffect(()=>{
    setData(null);setAudit([]);setMessage('');setConfirm(null);setLocationId('');
    if(uuid.test(organization.trim()))void refresh(organization.trim());
    else setState('idle');
  },[organization]);

  async function execute(path:string,payload:unknown,success:string) {
    if(busy||!validOrg)return;
    setBusy(true);setMessage('');
    try {
      const response=await post(root+path,payload);
      if(!response.ok){
        setMessage(response.status===409?'409 — операция конфликтует с активными записями, сменами или назначениями.'
          :[401,403,404].includes(response.status)?'403 — нет полномочий ADMIN этой организации.'
          :response.status===422?'422 — проверьте код, часовой пояс и даты.'
          :'Операция не выполнена ('+response.status+').');
        if([401,403,404].includes(response.status)){setData(null);setAudit([]);setState('denied');}
        return;
      }
      setMessage(success);
      setConfirm(null);
      await refresh();
    }catch{
      setMessage('Ошибка соединения. Проверьте локальный стенд и сессию.');
    }finally{setBusy(false);}
  }

  const activeLocations=data?.locations.filter(x=>!x.disabled)??[];
  const locationLabel=(id:string)=>data?.locations.find(x=>x.locationId===id)?.code??id;
  const iso=(value:string)=>Number.isFinite(Date.parse(value))?new Date(value).toLocaleString('ru-RU'):'—';

  return <section className="ui-page crm-workspace" aria-label="Управление организацией и филиалами">
    <div className="crm-heading">
      <div><span className="ui-eyebrow">БОКСРИТМ / АДМИНИСТРИРОВАНИЕ</span>
        <h1>Организация, филиалы и посты</h1>
        <p>Закрытый synthetic-контур. Все изменения требуют полномочия ADMIN конкретной организации.</p>
      </div><span className="status-badge">DEV / TEST</span>
    </div>
    <div className="form-grid">
      <label>UUID организации
        <input aria-label="UUID организации" value={organization}
          onChange={e=>setOrganization(e.target.value)} placeholder="00000000-0000-4000-8000-000000000000"/>
      </label>
      <button type="button" disabled={!validOrg||state==='loading'} onClick={()=>void refresh()}>
        Обновить структуру</button>
    </div>
    {state==='loading'&&<p role="status">Загружаем организацию…</p>}
    {state==='denied'&&<p role="alert">Доступ запрещён. Нужна активная сессия и роль ADMIN этой организации.</p>}
    {state==='error'&&<p role="alert">Не удалось загрузить структуру и журнал аудита.</p>}
    {message&&<p role="status" aria-live="polite">{message}</p>}
    {state==='ready'&&data&&<>
      <section aria-labelledby="branches-title">
        <h2 id="branches-title">Филиалы</h2>
        <p>Часовой пояс задаётся при создании и фиксируется после появления первой смены или брони.</p>
        <form onSubmit={e=>{e.preventDefault();void execute('/locations',{code:branchCode.trim(),timeZone},'Филиал создан.');}}>
          <label>Код филиала
            <input value={branchCode} onChange={e=>setBranchCode(e.target.value.toUpperCase())}
              pattern="[A-Z0-9][A-Z0-9_-]{1,31}" minLength={2} maxLength={32} required/>
          </label>
          <label>Часовой пояс IANA
            <input value={timeZone} onChange={e=>setTimeZone(e.target.value)} required/>
          </label>
          <button type="submit" disabled={busy||!branchCode.trim()||!timeZone.trim()}>Создать филиал</button>
        </form>
        <div className="table-scroll" role="region" aria-label="Список филиалов" tabIndex={0}>
          <table><thead><tr><th>Код</th><th>Часовой пояс</th><th>UUID</th><th>Состояние</th><th>Действие</th></tr></thead>
            <tbody>{data.locations.map(x=><tr key={x.locationId}>
              <td>{x.code}</td><td>{x.timeZone}</td><td><code>{x.locationId}</code></td>
              <td>{x.disabled?'Отключён':'Активен'}</td>
              <td>{!x.disabled&&(confirm?.kind==='location'&&confirm.id===x.locationId?
                <><button type="button" disabled={busy} onClick={()=>void execute('/locations/'+x.locationId+'/disable',undefined,'Филиал отключён.')}>Подтвердить отключение</button>
                  <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
                <button type="button" disabled={busy} onClick={()=>setConfirm({kind:'location',id:x.locationId,locationId:x.locationId})}>Отключить</button>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {data.locationsTruncated&&<p>Показаны первые 100 филиалов.</p>}
      </section>

      <section aria-labelledby="bays-title">
        <h2 id="bays-title">Рабочие посты</h2>
        <form onSubmit={e=>{e.preventDefault();void execute('/locations/'+locationId+'/bays',{code:bayCode.trim()},'Рабочий пост создан.');}}>
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
          <button type="submit" disabled={busy||!locationId||!bayCode.trim()}>Создать пост</button>
        </form>
        <div className="table-scroll" role="region" aria-label="Список рабочих постов" tabIndex={0}>
          <table><thead><tr><th>Филиал</th><th>Код поста</th><th>UUID</th><th>Состояние</th><th>Действие</th></tr></thead>
            <tbody>{data.bays.map(x=><tr key={x.bayId}>
              <td>{locationLabel(x.locationId)}</td><td>{x.code}</td><td><code>{x.bayId}</code></td>
              <td>{x.disabled?'Отключён':'Активен'}</td>
              <td>{!x.disabled&&(confirm?.kind==='bay'&&confirm.id===x.bayId?
                <><button type="button" disabled={busy} onClick={()=>void execute('/locations/'+x.locationId+'/bays/'+x.bayId+'/disable',undefined,'Пост отключён.')}>Подтвердить отключение</button>
                  <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
                <button type="button" disabled={busy} onClick={()=>setConfirm({kind:'bay',id:x.bayId,locationId:x.locationId})}>Отключить</button>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {data.baysTruncated&&<p>Показаны первые 100 постов.</p>}
      </section>

      <section aria-labelledby="shifts-title">
        <h2 id="shifts-title">Смены сотрудников</h2>
        <p>Сотрудник должен иметь действующее назначение в этой организации или филиале. Время вводится в ISO 8601 с явным смещением UTC.</p>
        <form onSubmit={e=>{e.preventDefault();void execute('/locations/'+locationId+'/shifts',
          {userId:employeeId.trim(),startsAt:startsAt.trim(),endsAt:endsAt.trim()},'Смена создана.');}}>
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
          <button type="submit" disabled={busy||!locationId||!uuid.test(employeeId.trim())||
            !startsAt.trim()||!endsAt.trim()}>Назначить смену</button>
        </form>
        <div className="table-scroll" role="region" aria-label="Список смен" tabIndex={0}>
          <table><thead><tr><th>Филиал</th><th>Сотрудник UUID</th><th>Начало</th><th>Окончание</th><th>Состояние</th><th>Действие</th></tr></thead>
            <tbody>{data.shifts.map(x=><tr key={x.shiftId}>
              <td>{locationLabel(x.locationId)}</td><td><code>{x.userId}</code></td>
              <td>{iso(x.startsAt)}</td><td>{iso(x.endsAt)}</td>
              <td>{x.canceled?'Отменена':'Активна'}</td>
              <td>{!x.canceled&&(confirm?.kind==='shift'&&confirm.id===x.shiftId?
                <><button type="button" disabled={busy} onClick={()=>void execute('/locations/'+x.locationId+'/shifts/'+x.shiftId+'/cancel',undefined,'Смена отменена.')}>Подтвердить отмену</button>
                  <button type="button" onClick={()=>setConfirm(null)}>Отмена</button></>:
                <button type="button" disabled={busy} onClick={()=>setConfirm({kind:'shift',id:x.shiftId,locationId:x.locationId})}>Отменить</button>)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {data.shiftsTruncated&&<p>Показаны первые 100 смен.</p>}
      </section>

      <section aria-labelledby="workshop-audit-title">
        <h2 id="workshop-audit-title">Журнал административных операций</h2>
        <p>Последние 100 событий создания и отключения филиалов/постов и назначения/отмены смен. Журнал неизменяемый.</p>
        <div className="table-scroll" role="region" aria-label="Журнал административных операций" tabIndex={0}>
          <table><thead><tr><th>Время</th><th>Операция</th><th>Объект</th><th>Актор UUID</th></tr></thead>
            <tbody>{audit.map(x=><tr key={x.eventId}>
              <td>{iso(x.createdAt)}</td><td>{x.action}</td><td>{x.entityType} · <code>{x.entityId}</code></td>
              <td><code>{x.actorId}</code></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>
    </>}
  </section>;
}
