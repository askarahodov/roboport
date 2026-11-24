import React from 'react';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Grant = { grantId: string; userId: string; role: string; locationId: string | null };
type AuditEvent = { eventId: string; grantId: string; actorId: string; userId: string; action: 'GRANTED' | 'REVOKED'; role: string; locationId: string | null; createdAt: string };
type StaffInvitation = { invitationId:string; targetLogin:string; role:string; locationId:string|null; status:'PENDING'|'ACCEPTED'|'REVOKED'|'EXPIRED'; createdAt:string; expiresAt:string; grantId:string|null };
type InvitationEvent = { eventId:string; invitationId:string; actorId:string; action:'CREATED'|'ACCEPTED'|'REVOKED'|'EXPIRED'; createdAt:string; targetLogin:string; role:string; locationId:string|null };
const staffRoles = ['MANAGER', 'INTAKE', 'MECHANIC', 'WAREHOUSE', 'CASHIER'] as const;
const roles = ['MANAGER', 'INTAKE', 'MECHANIC', 'WAREHOUSE', 'CASHIER'] as const;
const documents = ['INTAKE_ACT','ESTIMATE','HANDOVER','SUPPLIER_ORDER','FINANCE'] as const;
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

function readNextCursor(value:object, count:number, limit:number):string|null {
  if (!('nextCursor' in value)) return null;
  if (typeof value.nextCursor!=='string'||! /^[A-Za-z0-9_-]{1,256}$/.test(value.nextCursor)
      ||count!==limit) throw Error('INVALID_CURSOR');
  return value.nextCursor;
}

function cursorSuffix(cursor:string|null):string {
  return cursor ? '&cursor='+encodeURIComponent(cursor) : '';
}

async function mutate(url: string, payload: unknown, signal:AbortSignal) {
  const csrf = await fetch('/api/v1/auth/csrf', { method: 'POST', credentials: 'include', cache: 'no-store', signal });
  if ([401,403,404].includes(csrf.status)) return csrf;
  if (!csrf.ok) throw new Error('Сессия недоступна или истекла');
  const data: unknown = await csrf.json();
  if (!data || typeof data !== 'object' || !('csrfToken' in data) ||
      typeof data.csrfToken !== 'string') throw new Error('Не удалось проверить CSRF');
  return fetch(url, { method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': data.csrfToken },
    body: JSON.stringify(payload), signal });
}

export function AutoStaffAdmin() {
  const [organization, setOrganization] = React.useState('');
  const [user, setUser] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [role, setRole] = React.useState<typeof staffRoles[number]>('INTAKE');
  const [items, setItems] = React.useState<Grant[]>([]);
  const [audit, setAudit] = React.useState<AuditEvent[]>([]);
  const [auditState, setAuditState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [auditLimit, setAuditLimit] = React.useState(30);
  const [grantCursor,setGrantCursor] = React.useState<{org:string;cursor:string}|null>(null);
  const [grantNext,setGrantNext] = React.useState<string|null>(null);
  const [auditNext,setAuditNext] = React.useState<string|null>(null);
  const [inviteNext,setInviteNext] = React.useState<string|null>(null);
  const [inviteAuditNext,setInviteAuditNext] = React.useState<string|null>(null);
  const auditRequest = React.useRef(0);
  const inviteRequest = React.useRef(0);
  const inviteAuditRequest = React.useRef(0);
  const [state, setState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [busy, setBusy] = React.useState(false);
  const [confirmId, setConfirmId] = React.useState<string | null>(null);
  const [message, setMessage] = React.useState('');
  const [refresh, setRefresh] = React.useState(0);
  const [inviteLogin,setInviteLogin] = React.useState('');
  const [inviteRole,setInviteRole] = React.useState<typeof staffRoles[number]>('INTAKE');
  const [inviteLocation,setInviteLocation] = React.useState('');
  const [inviteDays,setInviteDays] = React.useState(7);
  const [invitations,setInvitations] = React.useState<StaffInvitation[]>([]);
  const [inviteState,setInviteState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [inviteBusy,setInviteBusy] = React.useState(false);
  const [inviteMessage,setInviteMessage] = React.useState('');
  const [inviteToken,setInviteToken] = React.useState('');
  const [inviteConfirmId,setInviteConfirmId] = React.useState<string|null>(null);
  const [inviteAudit,setInviteAudit] = React.useState<InvitationEvent[]>([]);
  const [inviteAuditState,setInviteAuditState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [policyRole,setPolicyRole] = React.useState<typeof roles[number]>('MANAGER');
  const [policyDoc,setPolicyDoc] = React.useState<DocumentKind>('ESTIMATE');
  const [policyCap,setPolicyCap] = React.useState<Capability>('READ');
  const [policyAllowed,setPolicyAllowed] = React.useState(false);
  const [policyLocation,setPolicyLocation] = React.useState('');
  const [policyUser,setPolicyUser] = React.useState('');
  const [policyDate,setPolicyDate] = React.useState('');
  const [policyEvents,setPolicyEvents] = React.useState<PolicyEvent[]>([]);
  const [policyCursor,setPolicyCursor] = React.useState<string|null>(null);
  const [policyTrail,setPolicyTrail] = React.useState<string[]>([]);
  const [policyNextBefore,setPolicyNextBefore] = React.useState<string|null>(null);
  const policyRequest = React.useRef(0);
  const [policyState,setPolicyState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [policyBusy,setPolicyBusy] = React.useState(false);
  const [policyMessage,setPolicyMessage] = React.useState('');
  const [policyPending,setPolicyPending] = React.useState<PendingPolicy|null>(null);
  const [effective,setEffective] = React.useState<EffectivePolicy|null>(null);
  const [effectiveState,setEffectiveState] = React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const validOrg = uuid.test(organization.trim());
  const organizationRef = React.useRef(organization.trim());
  organizationRef.current = organization.trim();
  const access = React.useRef(new AbortController());
  const effectiveRequest = React.useRef(0);
  const grantRequest = React.useRef<AbortController|null>(null);
  const [denialReason,setDenialReason] = React.useState('');
  const resetAccess = React.useCallback(()=>{
    // Единое поколение авторизации: старые запросы не могут восстановить
    // журнал или секрет после отказа либо перехода между организациями.
    access.current.abort();access.current=new AbortController();
    grantRequest.current?.abort();
    auditRequest.current++;inviteRequest.current++;inviteAuditRequest.current++;
    policyRequest.current++;effectiveRequest.current++;
    setItems([]);setGrantNext(null);setAuditNext(null);setInviteNext(null);setInviteAuditNext(null);
    setConfirmId(null);setAudit([]);setAuditState('idle');setMessage('');
    setInvitations([]);setInviteState('idle');setInviteToken('');setInviteMessage('');
    setInviteConfirmId(null);setInviteAudit([]);setInviteAuditState('idle');
    setPolicyEvents([]);setPolicyCursor(null);setPolicyTrail([]);
    setPolicyNextBefore(null);setPolicyState('idle');
    setPolicyPending(null);setPolicyMessage('');
    setEffective(null);setEffectiveState('idle');
    setBusy(false);setInviteBusy(false);setPolicyBusy(false);
    setUser('');setLocation('');setInviteLogin('');setInviteLocation('');
    setPolicyUser('');setPolicyLocation('');setPolicyDate('');
  },[]);
  const denyAll = React.useCallback((reason='Доступ запрещён или dev API отключён.')=>{
    resetAccess();setDenialReason(reason);setState('denied');
  },[resetAccess]);
  React.useEffect(()=>{
    if(access.current.signal.aborted)access.current=new AbortController();
    return ()=>{access.current.abort();grantRequest.current?.abort();};
  },[]);
  React.useEffect(()=>{
    resetAccess();setGrantCursor(null);setDenialReason('');
  },[organization,resetAccess]);
  React.useEffect(()=>{
    effectiveRequest.current++;setEffective(null);setEffectiveState('idle');
  },[organization,policyDoc,policyLocation,policyUser]);

  React.useEffect(() => {
    if (!validOrg) { setItems([]); setState('idle'); return; }
    const controller = new AbortController();grantRequest.current=controller;
    const signal=access.current.signal;
    setItems([]);setGrantNext(null);setConfirmId(null); setState('loading');
    void (async () => {
      try {
        const response = await fetch('/api/v1/dev/auto/organizations/' + encodeURIComponent(organization.trim()) + '/staff' +
          (grantCursor?.org===organization.trim()?'?cursor='+encodeURIComponent(grantCursor.cursor):''),
          { credentials: 'include', cache: 'no-store', signal: controller.signal });
        if (controller.signal.aborted||signal.aborted) return;
        if ([401, 403, 404].includes(response.status)) {denyAll();return;}
        if (!response.ok) throw new Error();
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('items' in data) || !Array.isArray(data.items)
          || data.items.length>100 || !data.items.every((item: unknown) => item && typeof item === 'object'
            && 'grantId' in item && typeof item.grantId === 'string' && uuid.test(item.grantId)
            && 'userId' in item && typeof item.userId === 'string' && uuid.test(item.userId)
            && 'role' in item && typeof item.role === 'string' && staffRoles.some(r => r === item.role)
            && 'locationId' in item && (item.locationId === null || typeof item.locationId === 'string' && uuid.test(item.locationId)))) throw new Error();
        const next=readNextCursor(data,data.items.length,100);
        if (!controller.signal.aborted&&!signal.aborted) { setItems(data.items as Grant[]);setGrantNext(next); setState('ready'); }
      } catch { if (!controller.signal.aborted&&!signal.aborted) setState('error'); }
    })();
    return () => controller.abort();
  }, [organization, refresh, validOrg,grantCursor,denyAll]);

  const loadInvitations = React.useCallback(async (cursor:string|null=null) => {
    if (!validOrg) return;
    const currentOrg=organization.trim();
    const sequence=++inviteRequest.current,signal=access.current.signal;
    setInviteNext(null);setInvitations([]);setInviteState('loading');
    try {
      const response=await fetch('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(currentOrg)+'/staff-invitations?limit=100'+cursorSuffix(cursor),
        {credentials:'include',cache:'no-store',signal});
      if(signal.aborted||organizationRef.current!==currentOrg||sequence!==inviteRequest.current)return;
      if([401,403,404].includes(response.status)){denyAll('403 — список приглашений недоступен. Доступ запрещён.');return;}
      if(!response.ok)throw new Error();
      const body:unknown=await response.json();
      if(!body||typeof body!=='object'||!('items' in body)||!Array.isArray(body.items)||
        body.items.length>100||!body.items.every((item:unknown)=>{
          if(!item||typeof item!=='object')return false;
          const v=item as StaffInvitation;
          return uuid.test(v.invitationId)&&typeof v.targetLogin==='string'&&
            staffRoles.includes(v.role as typeof staffRoles[number])&&
            (v.locationId===null||typeof v.locationId==='string'&&uuid.test(v.locationId))&&
            ['PENDING','ACCEPTED','REVOKED','EXPIRED'].includes(v.status)&&
            typeof v.createdAt==='string'&&Number.isFinite(Date.parse(v.createdAt))&&
            typeof v.expiresAt==='string'&&Number.isFinite(Date.parse(v.expiresAt))&&
            (v.grantId===null||typeof v.grantId==='string'&&uuid.test(v.grantId));
        }))throw new Error();
      const next=readNextCursor(body,body.items.length,100);
      if(signal.aborted||organizationRef.current!==currentOrg||sequence!==inviteRequest.current)return;
      setInviteNext(next);setInvitations(body.items as StaffInvitation[]);setInviteState('ready');
    }catch{if(!signal.aborted&&organizationRef.current===currentOrg&&sequence===inviteRequest.current)setInviteState('error');}
  },[organization,validOrg,denyAll]);

  // Список приглашений загружается по явному действию администратора:
  // открытие старого экрана назначений не должно создавать побочные запросы.

  async function createInvitation(event:React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(inviteBusy||!validOrg||!inviteLogin.trim()||
       inviteLocation.trim()&&!uuid.test(inviteLocation.trim()))return;
    const org=organization.trim(),signal=access.current.signal;
    setInviteBusy(true);setInviteMessage('');setInviteToken('');
    try{
      const response=await mutate('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/staff-invitations',{
          targetLogin:inviteLogin.trim().toLowerCase(),role:inviteRole,
          locationId:inviteLocation.trim()||null,expiresInDays:inviteDays,
        },signal);
      if(signal.aborted||org!==organizationRef.current)return;
      if(response.status===409){setInviteMessage('409 — активное назначение или приглашение уже существует.');return;}
      if([401,403,404].includes(response.status)){denyAll('403 — приглашение не разрешено сервером. Доступ запрещён.');return;}
      if(response.status===422){setInviteMessage('422 — проверьте логин, роль, филиал и срок действия.');return;}
      if(!response.ok)throw new Error();
      const value:unknown=await response.json();
      if(!value||typeof value!=='object'||!('invitationId' in value)||
        typeof value.invitationId!=='string'||!uuid.test(value.invitationId)||
        !('token' in value)||typeof value.token!=='string'||
        !/^[A-Za-z0-9_-]{40,64}$/.test(value.token)||
        !('expiresAt' in value)||typeof value.expiresAt!=='string'||
        !Number.isFinite(Date.parse(value.expiresAt)))throw new Error();
      if(signal.aborted||org!==organizationRef.current)return;
      setInviteToken(value.token);
      setInviteMessage('Приглашение создано. Одноразовый код показан только сейчас.');
      inviteAuditRequest.current++;setInviteAudit([]);setInviteAuditNext(null);setInviteAuditState('idle');
      await loadInvitations();
    }catch{
      if(!signal.aborted&&org===organizationRef.current)
        setInviteMessage('Результат неизвестен. Проверьте список; при необходимости отзовите приглашение и создайте новое.');
    }finally{if(!signal.aborted)setInviteBusy(false);}
  }

  async function revokeInvitation(invitationId:string) {
    if(inviteBusy||!validOrg)return;
    const org=organization.trim(),signal=access.current.signal;
    setInviteBusy(true);setInviteMessage('');
    try{
      const response=await mutate('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/staff-invitations/'+encodeURIComponent(invitationId)+'/revoke',{},signal);
      if(signal.aborted||org!==organizationRef.current)return;
      if([401,403,404].includes(response.status)){denyAll('403 — отзыв приглашения не разрешён. Доступ запрещён.');return;}
      if(!response.ok)throw new Error();
      const value=await response.json() as {status?:string};
      if(signal.aborted||org!==organizationRef.current)return;
      setInviteConfirmId(null);
      setInviteMessage(value.status==='REVOKED'?'Приглашение отозвано.':'Приглашение уже закрыто: '+String(value.status));
      inviteAuditRequest.current++;setInviteAudit([]);setInviteAuditNext(null);setInviteAuditState('idle');
      await loadInvitations();
    }catch{
      if(!signal.aborted&&org===organizationRef.current)setInviteMessage('Не удалось подтвердить отзыв. Перечитайте список приглашений.');
    }finally{if(!signal.aborted)setInviteBusy(false);}
  }

  async function loadInvitationAudit(cursor:string|null=null) {
    if(!validOrg)return;
    const org=organization.trim();
    const sequence=++inviteAuditRequest.current,signal=access.current.signal;
    setInviteAuditNext(null);setInviteAudit([]);setInviteAuditState('loading');
    try{
      const response=await fetch('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/staff-invitations/audit?limit=100'+cursorSuffix(cursor),
        {credentials:'include',cache:'no-store',signal});
      if(signal.aborted||organizationRef.current!==org||sequence!==inviteAuditRequest.current)return;
      if([401,403,404].includes(response.status)){denyAll('403 — журнал приглашений недоступен. Доступ запрещён.');return;}
      if(!response.ok)throw new Error();
      const body:unknown=await response.json();
      if(!body||typeof body!=='object'||!('items' in body)||!Array.isArray(body.items)||
        body.items.length>100||!body.items.every((item:unknown)=>{
          if(!item||typeof item!=='object')return false;
          const v=item as InvitationEvent;
          return uuid.test(v.eventId)&&uuid.test(v.invitationId)&&uuid.test(v.actorId)&&
            ['CREATED','ACCEPTED','REVOKED','EXPIRED'].includes(v.action)&&
            typeof v.targetLogin==='string'&&staffRoles.includes(v.role as typeof staffRoles[number])&&
            (v.locationId===null||typeof v.locationId==='string'&&uuid.test(v.locationId))&&
            typeof v.createdAt==='string'&&Number.isFinite(Date.parse(v.createdAt));
        }))throw new Error();
      const next=readNextCursor(body,body.items.length,100);
      if(signal.aborted||organizationRef.current!==org||sequence!==inviteAuditRequest.current)return;
      setInviteAuditNext(next);setInviteAudit(body.items as InvitationEvent[]);setInviteAuditState('ready');
    }catch{if(!signal.aborted&&organizationRef.current===org&&sequence===inviteAuditRequest.current)setInviteAuditState('error');}
  }

  async function loadAudit(cursor:string|null=null) {
    if (!validOrg) return;
    const currentOrg = organization.trim();
    const sequence=++auditRequest.current,signal=access.current.signal;
    setAuditNext(null);setAudit([]); setAuditState('loading');
    try {
      const response = await fetch('/api/v1/dev/auto/organizations/' +
        encodeURIComponent(currentOrg) + '/staff/audit?limit=' + auditLimit + cursorSuffix(cursor),
        { credentials: 'include', cache: 'no-store',signal });
      if(signal.aborted||organizationRef.current!==currentOrg||sequence!==auditRequest.current)return;
      if ([401,403,404].includes(response.status)) { denyAll('403 — Доступ к журналу запрещён.'); return; }
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
      const next=readNextCursor(body,body.items.length,auditLimit);
      if(signal.aborted||organizationRef.current!==currentOrg||sequence!==auditRequest.current)return;
      setAuditNext(next);setAudit(body.items as AuditEvent[]); setAuditState('ready');
    } catch { if(!signal.aborted&&organizationRef.current===currentOrg&&sequence===auditRequest.current) setAuditState('error'); }
  }

  async function execute(path: string, payload: unknown, action: 'grant'|'revoke') {
    if (busy || !validOrg) return;
    const signal=access.current.signal;
    setBusy(true); setMessage('');
    try {
      const res = await mutate(path, payload,signal);
      if(signal.aborted)return;
      if([401,403,404].includes(res.status)){denyAll('403 — операция не разрешена сервером. Доступ запрещён.');return;}
      if (!res.ok) {
        setMessage(res.status === 409 ? '409 — такое активное назначение уже существует.'
          : [401,403,404].includes(res.status) ? '403 — операция не разрешена сервером.'
          : 'Операция не выполнена (' + res.status + ').');
        return;
      }
      setMessage(action === 'grant' ? 'Назначение создано.' : 'Доступ сотрудника отозван.');
      auditRequest.current++;setAuditNext(null);setAudit([]);setAuditState('idle');
      setGrantCursor(null);setRefresh(n => n + 1);
    } catch { if(!signal.aborted)setMessage('Ошибка соединения. Проверьте локальный стенд и сессию.'); }
    finally { if(!signal.aborted)setBusy(false); }
  }


  async function loadPolicies(cursor:string|null=null,trail:string[]=[]){
    if(!validOrg)return;
    const org=organization.trim(),sequence=++policyRequest.current,signal=access.current.signal;
    setPolicyEvents([]);setPolicyNextBefore(null);
    setPolicyState('loading');setPolicyMessage('');
    try{
      const q=new URLSearchParams({limit:'50'});
      if(cursor)q.set('before',cursor);
      const res=await fetch('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/access-policies?'+q.toString(),{
          credentials:'include',cache:'no-store',signal,
        });
      if(signal.aborted||organizationRef.current!==org||sequence!==policyRequest.current)return;
      if([401,403,404].includes(res.status)){
        denyAll('403 — журнал ограничений недоступен. Доступ запрещён.');return;
      }
      if(!res.ok)throw Error('HTTP');
      const value:unknown=await res.json();
      if(!value||typeof value!=='object'||!('syntheticOnly' in value)||
        value.syntheticOnly!==true||!('items' in value)||
        !Array.isArray(value.items)||value.items.length>50||
        ('nextBefore' in value&&(typeof value.nextBefore!=='string'||
          !uuid.test(value.nextBefore)||value.items.length!==50))||
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
      if(signal.aborted||organizationRef.current!==org||sequence!==policyRequest.current)return;
      setPolicyEvents(value.items as PolicyEvent[]);
      setPolicyNextBefore('nextBefore' in value ? value.nextBefore as string : null);
      setPolicyTrail(trail);setPolicyCursor(cursor);setPolicyState('ready');
    }catch{
      if(!signal.aborted&&organizationRef.current===org&&sequence===policyRequest.current)
        setPolicyState('error');
    }
  }
  async function loadEffective(){
    if(!validOrg||!uuid.test(policyUser.trim())||
       !uuid.test(policyLocation.trim()))return;
    const org=organization.trim(),loc=policyLocation.trim(),user=policyUser.trim();
    const doc=policyDoc,sequence=++effectiveRequest.current,signal=access.current.signal;
    setEffective(null);setEffectiveState('loading');
    try{
      const q=new URLSearchParams({userId:user,locationId:loc,documentKind:doc});
      const response=await fetch('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(org)+'/access-policies/effective?'+q.toString(),{
          credentials:'include',cache:'no-store',signal,
        });
      if(signal.aborted||org!==organizationRef.current||sequence!==effectiveRequest.current)return;
      if([401,403,404].includes(response.status)){denyAll('403 — предпросмотр недоступен. Проверьте доступ и назначение сотрудника.');return;}
      if(!response.ok)throw Error('http');
      const value:unknown=await response.json();
      if(!value||typeof value!=='object'||!('syntheticOnly' in value)||
        value.syntheticOnly!==true||!('subjectToObjectACL' in value)||
        value.subjectToObjectACL!==true||!('capabilities' in value)||
        !value.capabilities||typeof value.capabilities!=='object'||
        !capabilities.every(cap=>cap in (value.capabilities as object) &&
          typeof (value.capabilities as EffectivePolicy['capabilities'])[cap]?.allowed==='boolean')
      )throw Error('effective');
      if(signal.aborted||org!==organizationRef.current||sequence!==effectiveRequest.current)return;
      const result=value as EffectivePolicy;
      if(result.userId!==user||result.locationId!==loc||result.documentKind!==doc||!Array.isArray(result.roles)||
        !result.roles.length||!result.roles.every(r=>roles.some(role=>role===r)))throw Error('effective');
      setEffective(result);setEffectiveState('ready');
    }catch{
      if(!signal.aborted&&org===organizationRef.current&&sequence===effectiveRequest.current)setEffectiveState('error');
    }
  }

  async function executePolicy(pending:PendingPolicy){
    if(policyBusy||pending.org!==organizationRef.current||!validOrg)return;
    const signal=access.current.signal;
    setPolicyBusy(true);setPolicyMessage('');
    try{
      const res=await mutate('/api/v1/dev/auto/organizations/'+
        encodeURIComponent(pending.org)+'/access-policies',JSON.parse(pending.body),signal);
      if(signal.aborted||pending.org!==organizationRef.current)return;
      if(res.status===409||res.status===422||[401,403,404].includes(res.status)){
        if([401,403,404].includes(res.status)){
          denyAll('403 — только ADMIN данной организации может менять ограничения. Доступ запрещён.');return;
        }
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
      if(!signal.aborted&&pending.org===organizationRef.current){
        setPolicyPending(pending);
        setPolicyMessage('Результат изменения неизвестен. Повторите исходный запрос с тем же ключом.');
      }
    }finally{if(!signal.aborted)setPolicyBusy(false);}
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
    <label>UUID организации <input value={organization} onChange={e => { if(e.target.value===organization)return;resetAccess();setState('idle');setOrganization(e.target.value); }}
      placeholder="UUID организации" aria-label="UUID организации" /></label>
    {state === 'idle' && <p>Укажите корректный UUID организации.</p>}
    {state === 'loading' && <p role="status">Загрузка назначений…</p>}
    {state === 'denied' && <><p role="alert">{denialReason||'Доступ запрещён или dev API отключён.'}</p>
      <button type="button" onClick={()=>{setGrantCursor(null);setRefresh(n=>n+1);}}>Проверить доступ заново</button></>}
    {state === 'error' && <p role="alert">Не удалось получить назначения.
      <button type="button" onClick={() => {setGrantCursor(null);setRefresh(n => n + 1);}}>Повторить</button></p>}
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
      <button type="button" onClick={()=>{setGrantCursor(null);setRefresh(n=>n+1);}}>Обновить назначения</button>
      {grantNext&&<button type="button" disabled={busy} onClick={()=>setGrantCursor({org:organization.trim(),cursor:grantNext})}>Следующие назначения</button>}
      <form onSubmit={e => {
        e.preventDefault();
        if (!uuid.test(user.trim()) || location.trim() && !uuid.test(location.trim())) return;
        void execute(base, { userId: user.trim(), role, locationId: location.trim() || null }, 'grant');
      }}>
        <h2>Назначить сотрудника</h2>
        <label>UUID пользователя <input required value={user} onChange={e => setUser(e.target.value)} /></label>
        <label>Роль <select value={role} onChange={e => setRole(e.target.value as typeof staffRoles[number])}>
          {staffRoles.map(r => <option key={r} value={r}>{r}</option>)}</select></label>
        <label>UUID филиала (необязательно) <input value={location} onChange={e => setLocation(e.target.value)} /></label>
        <button disabled={busy || !uuid.test(user.trim()) || !!location.trim() && !uuid.test(location.trim())} type="submit">
          Назначить</button>
      </form>
      <section aria-label="Приглашения сотрудников">
        <h2>Пригласить сотрудника</h2>
        <button type="button" disabled={inviteState==='loading'}
          onClick={()=>void loadInvitations()}>Обновить приглашения</button>
        <p>Приглашение адресуется уже существующей активной учётной записи. Сотрудник входит под тем же логином и принимает одноразовый код. Внешняя доставка и регистрация новых аккаунтов пока отключены.</p>
        <form onSubmit={createInvitation}>
          <label>Логин учётной записи
            <input required maxLength={128} value={inviteLogin}
              onChange={e=>setInviteLogin(e.target.value)} />
          </label>
          <label>Роль приглашения
            <select value={inviteRole} onChange={e=>setInviteRole(e.target.value as typeof staffRoles[number])}>
              {staffRoles.map(r=><option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label>UUID филиала приглашения (необязательно)
            <input value={inviteLocation} onChange={e=>setInviteLocation(e.target.value)} />
          </label>
          <label>Срок действия
            <select value={inviteDays} onChange={e=>setInviteDays(Number(e.target.value))}>
              <option value={1}>1 день</option><option value={7}>7 дней</option>
              <option value={14}>14 дней</option><option value={30}>30 дней</option>
            </select>
          </label>
          <button type="submit" disabled={inviteBusy||!inviteLogin.trim()||
            !!inviteLocation.trim()&&!uuid.test(inviteLocation.trim())}>
            Создать приглашение</button>
        </form>
        {inviteToken&&<section role="group" aria-label="Созданное приглашение">
          <p>Передайте код сотруднику по проверенному каналу. Сервер хранит только хеш; повторно показать этот код нельзя.</p>
          <input aria-label="Одноразовый код приглашения" readOnly value={inviteToken}/>
          <button type="button" onClick={()=>{
            if(!navigator.clipboard?.writeText){
              setInviteMessage('Буфер обмена недоступен. Выделите код вручную.');
              return;
            }
            void navigator.clipboard.writeText(inviteToken)
              .then(()=>setInviteMessage('Код скопирован.'))
              .catch(()=>setInviteMessage('Не удалось скопировать. Выделите код вручную.'));
          }}>
            Скопировать код</button>
          <a href="/staff/auto-invitation">Страница принятия приглашения</a>
        </section>}
        {inviteState==='loading'&&<p role="status">Загрузка приглашений…</p>}
        {inviteState==='denied'&&<p role="alert">Список приглашений недоступен.</p>}
        {inviteState==='error'&&<p role="alert">Не удалось загрузить приглашения.
          <button type="button" onClick={()=>void loadInvitations()}>Повторить</button></p>}
        {inviteState==='ready'&&(invitations.length?<ul>{invitations.map(item=><li key={item.invitationId}>
          <strong>{item.targetLogin}</strong> · {item.role} · {item.locationId??'Все филиалы'} ·
          {' '}{({PENDING:'Ожидает',ACCEPTED:'Принято',REVOKED:'Отозвано',EXPIRED:'Истекло'} as const)[item.status]} ·
          {' '}до {new Date(item.expiresAt).toLocaleString('ru-RU')}
          {item.grantId&&<> · назначение {item.grantId}</>}
          {item.status==='PENDING'&&(inviteConfirmId===item.invitationId?
            <span>
              <button type="button" disabled={inviteBusy} onClick={()=>void revokeInvitation(item.invitationId)}>
                Подтвердить отзыв приглашения</button>
              <button type="button" disabled={inviteBusy} onClick={()=>setInviteConfirmId(null)}>Отмена</button>
            </span>:
            <button type="button" disabled={inviteBusy} onClick={()=>setInviteConfirmId(item.invitationId)}>
              Отозвать приглашение</button>)}
        </li>)}</ul>:<p>Приглашений пока нет.</p>)}
        {inviteState==='ready'&&inviteNext&&<button type="button" disabled={inviteBusy} onClick={()=>void loadInvitations(inviteNext)}>Следующие приглашения</button>}
        <button type="button" disabled={inviteAuditState==='loading'}
          onClick={()=>void loadInvitationAudit()}>Показать события приглашений</button>
        {inviteAuditState==='loading'&&<p role="status">Загрузка журнала приглашений…</p>}
        {inviteAuditState==='denied'&&<p role="alert">Журнал приглашений недоступен.</p>}
        {inviteAuditState==='error'&&<p role="alert">Не удалось загрузить журнал приглашений.</p>}
        {inviteAuditState==='ready'&&(inviteAudit.length?<ol>{inviteAudit.map(event=><li key={event.eventId}>
          {({CREATED:'Создано',ACCEPTED:'Принято',REVOKED:'Отозвано',EXPIRED:'Истекло'} as const)[event.action]} ·
          {event.targetLogin} · {event.role} · {new Date(event.createdAt).toLocaleString('ru-RU')} · автор {event.actorId}
        </li>)}</ol>:<p>В журнале нет событий.</p>)}
        {inviteAuditState==='ready'&&inviteAuditNext&&<button type="button" onClick={()=>void loadInvitationAudit(inviteAuditNext)}>Следующие события приглашений</button>}
      </section>
      {inviteMessage&&<p role="status">{inviteMessage}</p>}
    </>}
    {state === 'ready' && <section aria-label="Журнал действий сотрудников">
      <h2>Журнал назначений и отзывов</h2>
      <p>Только подтверждённые сервером события этой организации. Отозванные назначения остаются в истории.</p>
      <label>Количество записей <select value={auditLimit} onChange={e => {auditRequest.current++;setAuditLimit(Number(e.target.value));setAuditNext(null);setAudit([]);setAuditState('idle');}}>
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
      {auditState==='ready'&&auditNext&&<button type="button" onClick={()=>void loadAudit(auditNext)}>Следующие события назначений</button>}
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
      {policyState==='ready'&&<p>Страница журнала ограничений: {policyTrail.length+1}.
        Записи хранятся неизменно; можно просмотреть старые версии.</p>}
      {policyState==='ready'&&(policyEvents.length?<ol>{policyEvents.map(p=>
        <li key={p.eventId}>
          <strong>{p.allowed?'ALLOW':'DENY'}</strong> · {p.role} · {p.documentKind} /
          {' '}{p.capability} · {p.locationId??'все филиалы'} ·
          {' '}{p.userId??'шаблон роли'} ·
          {' '}{p.notBefore?'с '+new Date(p.notBefore).toLocaleDateString('ru-RU'):'без ограничения истории'} ·
          {' '}{new Date(p.createdAt).toLocaleString('ru-RU')} · автор {p.actorId}
        </li>)}</ol>:<p>Для этой организации нет специальных ограничений.</p>)}
      {policyState==='ready'&&<div style={{display:'flex',flexWrap:'wrap',gap:'0.75rem'}}>
        {policyTrail.length>0&&<button type="button" disabled={policyBusy}
          onClick={()=>void loadPolicies(policyTrail[policyTrail.length-1]||null,
            policyTrail.slice(0,-1))}>Предыдущая страница правил</button>}
        {policyNextBefore&&<button type="button" disabled={policyBusy}
          onClick={()=>void loadPolicies(policyNextBefore,
            [...policyTrail,policyCursor??''])}>Более ранние правила</button>}
      </div>}
      {policyState!=='denied'&&<form onSubmit={e=>{e.preventDefault();savePolicy();}}>
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
      </form>}
      {policyState!=='denied'&&<section aria-label="Эффективные права сотрудника (предпросмотр)">
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
      </section>}
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
