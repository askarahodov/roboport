import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Grant = { grantId: string; userId: string; role: string; locationId: string | null };
type AuditEvent = { eventId: string; grantId: string; actorId: string; userId: string; action: 'GRANTED' | 'REVOKED'; role: string; locationId: string | null; createdAt: string };
const roles = ['MANAGER', 'INTAKE', 'MECHANIC', 'WAREHOUSE'] as const;
const documents = ['INTAKE_ACT','ESTIMATE','HANDOVER','SUPPLIER_ORDER'] as const;
const capabilities = ['READ','CREATE','EDIT','CLOSE','WRITE','PRICE_SALES','PRICE_PURCHASE','DISCOUNT'] as const;
type DocumentKind = typeof documents[number];
type Capability = typeof capabilities[number];
type PolicyEvent = { eventId:string; role:typeof roles[number];userId:string|null;
  locationId:string|null;documentKind:DocumentKind;capability:Capability;
  allowed:boolean;notBefore:string|null;actorId:string;createdAt:string };
type PendingPolicy = {org:string;body:string};
type EffectivePolicy = {syntheticOnly:true;subjectToObjectACL:true;
  documentKind:DocumentKind;userId:string;locationId:string;roles:string[];
  capabilities:Record<Capability,{allowed:boolean;notBefore:string|null}>};

async function mutate(url: string, payload: unknown) {
  const csrf = await fetch('/api/v1/auth/csrf', { method: 'POST', credentials: 'include', cache: 'no-store' });
  if (!csrf.ok) throw new Error('Сессия недоступна или истекла');
  const data: unknown = await csrf.json();
  if (!data || typeof data !== 'object' || !('csrfToken' in data) ||
      typeof data.csrfToken !== 'string') throw new Error('Не удалось проверить CSRF');
  return fetch(url, { method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': data.csrfToken },
    body: JSON.stringify(payload) });
}

export function AutoStaffAdmin() {
  const [organization, setOrganization] = React.useState('');
  const [user, setUser] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [role, setRole] = React.useState<typeof roles[number]>('INTAKE');
  const [items, setItems] = React.useState<Grant[]>([]);
  const [audit, setAudit] = React.useState<AuditEvent[]>([]);
  const [auditState, setAuditState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [auditLimit, setAuditLimit] = React.useState(30);
  const [state, setState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [busy, setBusy] = React.useState(false);
  const [confirmId, setConfirmId] = React.useState<string | null>(null);
  const [message, setMessage] = React.useState('');
  const [refresh, setRefresh] = React.useState(0);
  const [policyRole,setPolicyRole] = React.useState<typeof roles[number]>('MANAGER');
  const [policyDoc,setPolicyDoc] = React.useState<DocumentKind>('ESTIMATE');
  const [policyCap,setPolicyCap] = React.useState<Capability>('READ');
  const [policyAllowed,setPolicyAllowed] = React.useState(false);
  const [policyLocation,setPolicyLocation] = React.useState('');
  const [policyUser,setPolicyUser] = React.useState('');
  const [policyDate,setPolicyDate] = React.useState('');
  const [policyEvents,setPolicyEvents] = React.useState<PolicyEvent[]>([]);
  const [policyState,setPolicyState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [policyBusy,setPolicyBusy] = React.useState(false);
  const [policyMessage,setPolicyMessage] = React.useState('');
  const [policyPending,setPolicyPending] = React.useState<PendingPolicy|null>(null);
  const [effective,setEffective] = React.useState<EffectivePolicy|null>(null);
  const [effectiveState,setEffectiveState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const validOrg = uuid.test(organization.trim());
  const organizationRef = React.useRef(organization.trim());
  organizationRef.current = organization.trim();
  React.useEffect(() => {
    setConfirmId(null);setAudit([]);setAuditState('idle');setMessage('');
    setPolicyEvents([]);setPolicyState('idle');setPolicyPending(null);setPolicyMessage('');
  }, [organization]);
  React.useEffect(()=>{
    setEffective(null);setEffectiveState('idle');
  },[organization,policyDoc,policyLocation,policyUser]);

  React.useEffect(() => {
    if (!validOrg) { setItems([]); setState('idle'); return; }
    const controller = new AbortController();
    setItems([]); setState('loading');
    void (async () => {
      try {
        const response = await fetch('/api/v1/dev/auto/organizations/' + encodeURIComponent(organization.trim()) + '/staff',
          { credentials: 'include', cache: 'no-store', signal: controller.signal });
        if (controller.signal.aborted) return;
        if ([401, 403, 404].includes(response.status)) { setState('denied'); return; }
        if (!response.ok) throw new Error();
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('items' in data) || !Array.isArray(data.items)
          || !data.items.every((item: unknown) => item && typeof item === 'object'
            && 'grantId' in item && typeof item.grantId === 'string' && uuid.test(item.grantId)
            && 'userId' in item && typeof item.userId === 'string' && uuid.test(item.userId)
            && 'role' in item && typeof item.role === 'string' && roles.some(r => r === item.role)
            && 'locationId' in item && (item.locationId === null || typeof item.locationId === 'string' && uuid.test(item.locationId)))) throw new Error();
        if (!controller.signal.aborted) { setItems(data.items as Grant[]); setState('ready'); }
      } catch { if (!controller.signal.aborted) setState('error'); }
    })();
    return () => controller.abort();
  }, [organization, refresh, validOrg]);

  async function loadAudit() {
    if (!validOrg) return;
    const currentOrg = organization.trim();
    setAudit([]); setAuditState('loading');
    try {
      const response = await fetch('/api/v1/dev/auto/organizations/' +
        encodeURIComponent(currentOrg) + '/staff/audit?limit=' + auditLimit,
        { credentials: 'include', cache: 'no-store' });
      if (organizationRef.current !== currentOrg) return;
      if ([401,403,404].includes(response.status)) { setAuditState('denied'); return; }
      if (!response.ok) throw new Error();
      const body: unknown = await response.json();
      if (!body || typeof body !== 'object' || !('items' in body) || !Array.isArray(body.items) ||
          body.items.length > auditLimit || !body.items.every((e: unknown) =>
            !!e && typeof e === 'object' &&
            'eventId' in e && typeof e.eventId === 'string' && uuid.test(e.eventId) &&
            'grantId' in e && typeof e.grantId === 'string' && uuid.test(e.grantId) &&
            'actorId' in e && typeof e.actorId === 'string' && uuid.test(e.actorId) &&
            'userId' in e && typeof e.userId === 'string' && uuid.test(e.userId) &&
            'action' in e && (e.action === 'GRANTED' || e.action === 'REVOKED') &&
            'role' in e && typeof e.role === 'string' &&
            'createdAt' in e && typeof e.createdAt === 'string' &&
            'locationId' in e && (e.locationId === null ||
              typeof e.locationId === 'string' && uuid.test(e.locationId)))) throw new Error();
      setAudit(body.items as AuditEvent[]); setAuditState('ready');
    } catch { if (organizationRef.current === currentOrg) setAuditState('error'); }
  }

  async function execute(path: string, payload: unknown, action: 'grant'|'revoke') {
    if (busy || !validOrg) return;
    setBusy(true); setMessage('');
    try {
      const res = await mutate(path, payload);
      if (!res.ok) {
        setMessage(res.status === 409 ? '409 — такое активное назначение уже существует.'
          : [401,403,404].includes(res.status) ? '403 — операция не разрешена сервером.'
          : 'Операция не выполнена (' + res.status + ').');
        return;
      }
      setMessage(action === 'grant' ? 'Назначение создано.' : 'Доступ сотрудника отозван.');
      setAudit([]); setAuditState('idle');
      setRefresh(n => n + 1);
    } catch { setMessage('Ошибка соединения. Проверьте локальный стенд и сессию.'); }
    finally { setBusy(false); }
  }


  async function loadPolicies(){
    if(!validOrg)return;
    const org=organization.trim();
    setPolicyEvents([]);setPolicyState('loading');setPolicyMessage('');
    try{
      const res=await fetch('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/access-policies',{
          credentials:'include',cache:'no-store',
        });
      if(organizationRef.current!==org)return;
      if([401,403,404].includes(res.status)){setPolicyState('denied');return;}
      if(!res.ok)throw Error('HTTP');
      const value:unknown=await res.json();
      if(!value||typeof value!=='object'||!('syntheticOnly' in value)||
        value.syntheticOnly!==true||!('items' in value)||
        !Array.isArray(value.items)||value.items.length>100||
        !value.items.every((item:unknown)=>{
          if(!item||typeof item!=='object')return false;
          const p=item as PolicyEvent;
          return uuid.test(p.eventId)&&roles.includes(p.role)&&
            documents.includes(p.documentKind)&&capabilities.includes(p.capability)&&
            (p.userId===null||uuid.test(p.userId))&&
            (p.locationId===null||uuid.test(p.locationId))&&
            uuid.test(p.actorId)&&typeof p.allowed==='boolean'&&
            typeof p.createdAt==='string'&&Number.isFinite(Date.parse(p.createdAt))&&
            (p.notBefore===null||typeof p.notBefore==='string'&&
              Number.isFinite(Date.parse(p.notBefore)));
        }))throw Error('INVALID_POLICY');
      setPolicyEvents(value.items as PolicyEvent[]);setPolicyState('ready');
    }catch{
      if(organizationRef.current===org)setPolicyState('error');
    }
  }
  async function loadEffective(){
    if(!validOrg||!uuid.test(policyUser.trim())||
       !uuid.test(policyLocation.trim()))return;
    const org=organization.trim(),loc=policyLocation.trim(),user=policyUser.trim();
    const doc=policyDoc;
    setEffective(null);setEffectiveState('loading');
    try{
      const q=new URLSearchParams({userId:user,locationId:loc,documentKind:doc});
      const response=await fetch('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/access-policies/effective?'+q.toString(),{
          credentials:'include',cache:'no-store',
        });
      if(org!==organizationRef.current)return;
      if([401,403,404].includes(response.status)){setEffectiveState('denied');return;}
      if(!response.ok)throw Error('http');
      const value:unknown=await response.json();
      if(!value||typeof value!=='object'||!('syntheticOnly' in value)||
        value.syntheticOnly!==true||!('subjectToObjectACL' in value)||
        value.subjectToObjectACL!==true||!('capabilities' in value)||
        !value.capabilities||typeof value.capabilities!=='object'||
        !capabilities.every(cap=>cap in (value.capabilities as object) &&
          typeof (value.capabilities as EffectivePolicy['capabilities'])[cap]?.allowed==='boolean')
      )throw Error('effective');
      setEffective(value as EffectivePolicy);setEffectiveState('ready');
    }catch{
      if(org===organizationRef.current)setEffectiveState('error');
    }
  }

  async function executePolicy(pending:PendingPolicy){
    if(policyBusy||pending.org!==organizationRef.current||!validOrg)return;
    setPolicyBusy(true);setPolicyMessage('');
    try{
      const res=await mutate('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(pending.org)+'/access-policies',JSON.parse(pending.body));
      if(pending.org!==organizationRef.current)return;
      if(res.status===409||res.status===422||[401,403,404].includes(res.status)){
        setPolicyPending(null);
        setPolicyMessage(res.status===409?
          '409 — ключ уже использован для другого правила или неверен scope. Перечитайте журнал.':
          res.status===422?'422 — проверьте значения правила.':
          '403 — только ADMIN данной организации может менять ограничения.');
        return;
      }
      if(!res.ok)throw Error('unknown');
      setPolicyPending(null);
      setPolicyMessage('Ограничение сохранено сервером; прежние версии остались в аудите.');
      await loadPolicies();
    }catch{
      if(pending.org===organizationRef.current){
        setPolicyPending(pending);
        setPolicyMessage('Результат изменения неизвестен. Повторите исходный запрос с тем же ключом.');
      }
    }finally{setPolicyBusy(false);}
  }
  function savePolicy(){
    if(!validOrg||policyBusy||policyPending||
       policyUser.trim()&&!uuid.test(policyUser.trim())||
       policyLocation.trim()&&!uuid.test(policyLocation.trim()))return;
    const notBefore=policyCap==='READ'&&policyAllowed&&policyDate?
      new Date(policyDate+'T00:00:00.000Z').toISOString():null;
    const body=JSON.stringify({requestKey:crypto.randomUUID(),role:policyRole,
      locationId:policyLocation.trim()||null,userId:policyUser.trim()||null,
      documentKind:policyDoc,capability:policyCap,allowed:policyAllowed,notBefore});
    void executePolicy({org:organization.trim(),body});
  }

  const base = '/api/v1/dev/auto/organizations/' + encodeURIComponent(organization.trim()) + '/staff';
  return <section aria-label="Сотрудники организации (dev)" style={{maxWidth:'76rem',margin:'2rem auto',overflowWrap:'anywhere'}}>
    <h1>Сотрудники организации</h1>
    <p>Только локальный синтетический стенд. Права проверяет сервер, а не этот экран. Реальных сотрудников не добавляйте.</p>
    <label>UUID организации <input value={organization} onChange={e => { setOrganization(e.target.value); setMessage(''); }}
      placeholder="UUID организации" aria-label="UUID организации" /></label>
    {state === 'idle' && <p>Укажите корректный UUID организации.</p>}
    {state === 'loading' && <p role="status">Загрузка назначений…</p>}
    {state === 'denied' && <p role="alert">Доступ запрещён или dev API отключён.</p>}
    {state === 'error' && <p role="alert">Не удалось получить назначения.
      <button type="button" onClick={() => setRefresh(n => n + 1)}>Повторить</button></p>}
    {state === 'ready' && <><h2>Активные назначения</h2>
      {items.length ? <ul>{items.map(g => <li key={g.grantId}>
        <strong>{g.role}</strong> · {g.userId} · {g.locationId ?? 'Все филиалы'}
        {confirmId === g.grantId ? <span>
          <button type="button" disabled={busy} onClick={() => {
            setConfirmId(null);
            void execute(base + '/' + encodeURIComponent(g.grantId) + '/revoke', {}, 'revoke');
          }}>Подтвердить отзыв</button>
          <button type="button" disabled={busy} onClick={() => setConfirmId(null)}>Отмена</button>
        </span> : <button type="button" disabled={busy} onClick={() => setConfirmId(g.grantId)}>
          Отозвать
        </button>}</li>)}</ul> : <p>Активных назначений нет.</p>}
      <form onSubmit={e => {
        e.preventDefault();
        if (!uuid.test(user.trim()) || location.trim() && !uuid.test(location.trim())) return;
        void execute(base, { userId: user.trim(), role, locationId: location.trim() || null }, 'grant');
      }}>
        <h2>Назначить сотрудника</h2>
        <label>UUID пользователя <input required value={user} onChange={e => setUser(e.target.value)} /></label>
        <label>Роль <select value={role} onChange={e => setRole(e.target.value as typeof roles[number])}>
          {roles.map(r => <option key={r} value={r}>{r}</option>)}</select></label>
        <label>UUID филиала (необязательно) <input value={location} onChange={e => setLocation(e.target.value)} /></label>
        <button disabled={busy || !uuid.test(user.trim()) || !!location.trim() && !uuid.test(location.trim())} type="submit">
          Назначить</button>
      </form>
    </>}
    {state === 'ready' && <section aria-label="Журнал действий сотрудников">
      <h2>Журнал назначений и отзывов</h2>
      <p>Только подтверждённые сервером события этой организации. Отозванные назначения остаются в истории.</p>
      <label>Количество записей <select value={auditLimit} onChange={e => {setAuditLimit(Number(e.target.value)); setAudit([]); setAuditState('idle');}}>
        <option value={10}>10</option><option value={30}>30</option><option value={100}>100</option>
      </select></label>
      <button type="button" onClick={() => void loadAudit()} disabled={auditState === 'loading'}>Показать журнал</button>
      {auditState === 'loading' && <p role="status">Загрузка журнала…</p>}
      {auditState === 'denied' && <p role="alert">Доступ к журналу запрещён.</p>}
      {auditState === 'error' && <p role="alert">Не удалось загрузить журнал. Повторите запрос.</p>}
      {auditState === 'ready' && (audit.length ? <ul>{audit.map(event =>
        <li key={event.eventId}>{event.action === 'GRANTED' ? 'Назначение' : 'Отзыв'} ·
          {event.createdAt} · {event.role} · сотрудник {event.userId} ·
          исполнитель {event.actorId} · {event.locationId ?? 'Все филиалы'}
        </li>)}</ul> : <p>В журнале нет записей.</p>)}
    </section>}
    {state==='ready'&&<section aria-label="Гранулярные ограничения на документы">
      <h2>Ограничения документов и цен (только ADMIN, dev)</h2>
      <p>Правила могут только сужать действующие права роли, филиала и сотрудника.
        Разрешение ALLOW в исключении не отменяет DENY в шаблоне роли и не выдаёт новую роль.
        Все изменения сохраняются неизменно с автором и временем.</p>
      <button type="button" disabled={policyBusy||policyState==='loading'}
        onClick={()=>void loadPolicies()}>Показать ограничения документов</button>
      {policyState==='loading'&&<p role="status">Загрузка правил…</p>}
      {policyState==='denied'&&<p role="alert">403 — журнал ограничений недоступен.</p>}
      {policyState==='error'&&<p role="alert">Ошибка загрузки ограничений. Повторите запрос.</p>}
      {policyState==='ready'&&(policyEvents.length?<ol>{policyEvents.map(p=>
        <li key={p.eventId}>
          <strong>{p.allowed?'ALLOW':'DENY'}</strong> · {p.role} · {p.documentKind} /
          {' '}{p.capability} · {p.locationId??'все филиалы'} ·
          {' '}{p.userId??'шаблон роли'} ·
          {' '}{p.notBefore?'с '+new Date(p.notBefore).toLocaleDateString('ru-RU'):'без ограничения истории'} ·
          {' '}{new Date(p.createdAt).toLocaleString('ru-RU')} · автор {p.actorId}
        </li>)}</ol>:<p>Для этой организации нет специальных ограничений.</p>)}
      <form onSubmit={e=>{e.preventDefault();savePolicy();}}>
        <h3>Новая версия правила</h3>
        <div className="auto-claim-forms">
          <label>Роль политики
            <select aria-label="Роль политики" value={policyRole}
              onChange={e=>setPolicyRole(e.target.value as typeof policyRole)}>
              {roles.map(v=><option value={v} key={v}>{v}</option>)}
            </select>
          </label>
          <label>Документ политики
            <select aria-label="Документ политики" value={policyDoc}
              onChange={e=>setPolicyDoc(e.target.value as DocumentKind)}>
              {documents.map(v=><option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label>Действие политики
            <select aria-label="Действие политики" value={policyCap}
              onChange={e=>setPolicyCap(e.target.value as Capability)}>
              {capabilities.map(v=><option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label>Результат политики
            <select aria-label="Результат политики"
              value={policyAllowed?'ALLOW':'DENY'}
              onChange={e=>setPolicyAllowed(e.target.value==='ALLOW')}>
              <option value="DENY">DENY · запретить</option>
              <option value="ALLOW">ALLOW · снять запрет именно этого scope</option>
            </select>
          </label>
          <label>UUID филиала правила (опционально)
            <input aria-label="UUID филиала правила" value={policyLocation}
              onChange={e=>setPolicyLocation(e.target.value)}/>
          </label>
          <label>UUID сотрудника правила (опционально)
            <input aria-label="UUID сотрудника правила" value={policyUser}
              onChange={e=>setPolicyUser(e.target.value)}/>
          </label>
          {policyCap==='READ'&&policyAllowed&&<label>История с даты (UTC, опционально)
            <input type="date" aria-label="История с даты (UTC)"
              value={policyDate} onChange={e=>setPolicyDate(e.target.value)}/>
          </label>}
        </div>
        <button type="submit" disabled={policyBusy||!!policyPending||
          !!policyUser.trim()&&!uuid.test(policyUser.trim())||
          !!policyLocation.trim()&&!uuid.test(policyLocation.trim())}>
          Сохранить правило</button>
      </form>
      <section aria-label="Эффективные права сотрудника (предпросмотр)">
        <h3>Проверить эффективные права сотрудника</h3>
        <p>Нужны действующий ADMIN и UUID филиала/сотрудника выше.
          Предпросмотр показывает только права по роли и ограничениям,
          но не отменяет проверки конкретного заказ-наряда, VIN и сессии сотрудника.</p>
        <button type="button" disabled={!uuid.test(policyUser.trim())||
          !uuid.test(policyLocation.trim())||effectiveState==='loading'}
          onClick={()=>void loadEffective()}>Проверить эффективные права</button>
        {effectiveState==='loading'&&<p role="status">Проверка полномочий…</p>}
        {effectiveState==='denied'&&<p role="alert">403 — сотрудник не имеет действующего назначения либо организация чужая.</p>}
        {effectiveState==='error'&&<p role="alert">Не удалось вычислить полномочия.</p>}
        {effectiveState==='ready'&&effective&&<section aria-label="Результат эффективных прав">
          <p>Роли: {effective.roles.join(', ')}. Документ: {effective.documentKind}.</p>
          <ul>{capabilities.map(cap=><li key={cap}>
            {cap}: {effective.capabilities[cap].allowed?'Разрешено в пределах объекта':'Запрещено'}
            {effective.capabilities[cap].notBefore&&
              ' · История с '+new Date(effective.capabilities[cap].notBefore!).toLocaleDateString('ru-RU')}
          </li>)}</ul>
        </section>}
      </section>
      {policyPending&&<div role="group" aria-label="Повтор сохранения ограничения">
        <button type="button" disabled={policyBusy}
          onClick={()=>void executePolicy(policyPending)}>Повторить исходное правило</button>
        <button type="button" disabled={policyBusy}
          onClick={()=>{setPolicyPending(null);void loadPolicies();}}>
          Перечитать правила</button>
      </div>}
      {policyMessage&&<p role="status">{policyMessage}</p>}
    </section>}
    {message && <p role="status">{message}</p>}
  </section>;
}
