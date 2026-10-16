import * as React from 'react';
import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom';

type SessionState = 'loading' | 'authenticated' | 'anonymous' | 'unavailable';
type SectionId = 'overview' | 'robots' | 'tickets' | 'estimates' | 'documents' | 'settings';
const sections: Array<{ id: SectionId; label: string; description: string }> = [
  { id: 'overview', label: 'Обзор', description: 'Информация о ваших обращениях и обслуживании роботов.' },
  { id: 'robots', label: 'Роботы', description: 'Устройства, привязанные к вашему аккаунту.' },
  { id: 'tickets', label: 'Заявки', description: 'История ваших обращений в сервис.' },
  { id: 'estimates', label: 'Сметы', description: 'Сметы, доступные вам для ознакомления.' },
  { id: 'documents', label: 'Документы', description: 'Защищённые документы ваших заявок.' },
  { id: 'settings', label: 'Настройки и уведомления', description: 'Параметры вашего аккаунта.' },
];

type Ticket = { id: string; state: string; version: number };
function Tickets() {
  const [status, setStatus] = React.useState<'loading'|'ready'|'denied'|'unavailable'>('loading');
  const [items, setItems] = React.useState<Ticket[]>([]);
  const [retry, setRetry] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setStatus('loading');
      try {
        const response = await fetch('/api/v1/dev/account/collections/tickets', {
          credentials: 'include', cache: 'no-store', signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (controller.signal.aborted) return;
        if (response.status === 401 || response.status === 403) {
          setStatus('denied'); return;
        }
        if (!response.ok) { setStatus('unavailable'); return; }
        const json: unknown = await response.json();
        if (!json || typeof json !== 'object' || !('items' in json) ||
            !Array.isArray(json.items) || !json.items.every((item: unknown) =>
              item !== null && typeof item === 'object' &&
              'id' in item && typeof item.id === 'string' &&
              'state' in item && typeof item.state === 'string' &&
              'version' in item && typeof item.version === 'number')) {
          setStatus('unavailable'); return;
        }
        setItems(json.items as Ticket[]);
        setStatus('ready');
      } catch {
        if (!controller.signal.aborted) setStatus('unavailable');
      }
    }
    void load();
    return () => controller.abort();
  }, [retry]);
  return <section aria-labelledby="account-tickets">
    <h2 id="account-tickets">Заявки</h2>
    {status === 'loading' && <p role="status">Загрузка заявок…</p>}
    {status === 'denied' && <p role="alert">Сессия завершена или доступ отозван. Войдите повторно.</p>}
    {status === 'unavailable' && <><p role="alert">Не удалось получить заявки. Данные не показаны.</p>
      <button type="button" onClick={() => setRetry(value => value+1)}>Повторить загрузку</button></>}
    {status === 'ready' && (items.length === 0
      ? <p role="status">У вас пока нет доступных заявок.</p>
      : <ul>{items.map(item =>
          <li key={item.id}>Заявка {item.id} — {item.state}, версия {item.version}</li>)}</ul>)}
  </section>;
}

type CollectionKind = 'robots'|'estimates'|'documents';
type CollectionRow = {id: string;ticketId?: string;state?: string;version?: number;totalMinor?: number};
function CustomerCollection({kind,csrf}:{kind:CollectionKind;csrf:string|null}) {
  const [status,setStatus] = React.useState<'loading'|'ready'|'denied'|'unavailable'>('loading');
  const [items,setItems] = React.useState<CollectionRow[]>([]);
  const [retry,setRetry] = React.useState(0);
  const [decisionMessage,setDecisionMessage] = React.useState('');
  const [deciding,setDeciding] = React.useState(false);
  const keys=React.useRef<Record<string,string>>({});
  React.useEffect(() => {
    const controller=new AbortController();
    async function load() {
      setStatus('loading');
      try {
        const response=await fetch('/api/v1/dev/account/collections/'+kind,{
          credentials:'include',cache:'no-store',signal:controller.signal,
          headers:{Accept:'application/json'},
        });
        if (controller.signal.aborted) return;
        if (response.status===401 || response.status===403) {
          setItems([]);setStatus('denied');return;
        }
        if (!response.ok) {setItems([]);setStatus('unavailable');return;}
        const data:unknown=await response.json();
        if (!data || typeof data!=='object' || !('items' in data) ||
            !Array.isArray(data.items) || !data.items.every((x:unknown)=>
              x!==null && typeof x==='object' && 'id' in x && typeof x.id==='string')) {
          setItems([]);setStatus('unavailable');return;
        }
        setItems(data.items as CollectionRow[]);
        setStatus('ready');
      } catch { if (!controller.signal.aborted) {setItems([]);setStatus('unavailable');} }
    }
    void load();
    return () => controller.abort();
  },[kind,retry]);
  async function decide(row:CollectionRow,decision:'approve'|'decline') {
    if (!csrf || typeof row.version!=='number' || deciding || row.state!=='SENT') return;
    const slot=row.id+':'+row.version+':'+decision;
    keys.current[slot] ??= crypto.randomUUID();
    setDecisionMessage('');setDeciding(true);
    try {
      const response=await fetch('/api/v1/customer/estimates/'+row.id+'/'+decision,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,
                 'Idempotency-Key':keys.current[slot]},
        body:JSON.stringify({estimateVersion:row.version,
          ...(decision==='approve' ? {approvalMethod:'verified_account'} : {})}),
      });
      if (response.status===409) {
        setDecisionMessage('Версия сметы устарела. Обновите список перед решением.');
        setRetry(x=>x+1);
      } else if (response.status===401 || response.status===403 || response.status===404) {
        setItems([]);setStatus('denied');
        setDecisionMessage('Доступ к смете отозван или сессия завершена.');
      } else if (!response.ok) {
        setDecisionMessage('Решение не сохранено. Повторите запрос.');
      } else {
        setDecisionMessage('Решение зарегистрировано сервером.');
        setRetry(x=>x+1);
      }
    } catch {setDecisionMessage('Нет связи с сервером.');}
    finally {setDeciding(false);}
  }
  const label=sections.find(x=>x.id===kind)!.label;
  return <section aria-labelledby={'account-'+kind}>
    <h2 id={'account-'+kind}>{label}</h2>
    {status==='loading' && <p role="status">Загрузка раздела…</p>}
    {status==='denied' && <p role="alert">401 — доступ отозван или сессия завершена.</p>}
    {status==='unavailable' && <><p role="alert">Раздел недоступен. Приватные данные не показаны.</p>
      <button type="button" onClick={()=>setRetry(x=>x+1)}>Повторить загрузку</button></>}
    {status==='ready' && (items.length===0
      ? <p role="status">Доступных записей пока нет.</p>
      : <ul>{items.map(row=><li key={row.id}>
        {label}: {row.id}{row.state ? ' — '+row.state : ''}
        {row.version !== undefined ? ', версия '+row.version : ''}
        {row.totalMinor !== undefined ? ', сумма '+(row.totalMinor/100).toFixed(2)+' ₽' : ''}
        {kind==='estimates' && row.state==='SENT' && <div>
          {csrf ? <>
            <button disabled={deciding} onClick={()=>void decide(row,'approve')}>Согласовать</button>
            <button disabled={deciding} onClick={()=>void decide(row,'decline')}>Отклонить</button>
          </> : <p>Для решения по смете требуется повторный локальный вход.</p>}
        </div>}
      </li>)}</ul>)}
    {decisionMessage && <p role="alert">{decisionMessage}</p>}
    {kind==='documents' && <p>Для получения содержимого документа требуется защищённый backend. Прямого доступа к MinIO нет.</p>}
  </section>;
}

function Section({ id, csrf }: { id: SectionId; csrf:string|null }) {
  if (id === 'tickets') return <Tickets />;
  if (id === 'robots' || id === 'estimates' || id === 'documents') return <CustomerCollection kind={id} csrf={csrf} />;
  const section = sections.find(item => item.id === id)!;
  return <section aria-labelledby={'account-' + id}>
    <h2 id={'account-' + id}>{section.label}</h2>
    <p>{section.description}</p>
    <p role="status">Данные раздела пока недоступны. Приватная информация не загружается без серверной проверки доступа.</p>
  </section>;
}

function LocalLogin({onSuccess}:{onSuccess:(csrf:string)=>void}) {
  const [login,setLogin] = React.useState('');
  const [password,setPassword] = React.useState('');
  const [status,setStatus] = React.useState('');
  const [busy,setBusy] = React.useState(false);
  async function submit(event:React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);setStatus('');
    try {
      const response=await fetch('/api/v1/auth/login',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({login,password}),
      });
      if (!response.ok) {
        setStatus(response.status===429 ? 'Слишком много попыток. Повторите позже.' :
          response.status===403 ? 'Вход разрешён только на защищённом локальном стенде.' :
          'Неверные учётные данные или вход сейчас недоступен.');
        return;
      }
      const data:unknown=await response.json();
      if (!data || typeof data!=='object' || !('csrfToken' in data) ||
          typeof data.csrfToken!=='string' || data.csrfToken.length<20) {
        setStatus('Некорректный ответ сервера. Вход не подтверждён.');
        return;
      }
      onSuccess(data.csrfToken);
    } catch {setStatus('Не удалось связаться с сервером.');}
    finally {setPassword('');setBusy(false);}
  }
  return <section>
    <h2>Локальный вход</h2>
    <p>Доступно только для синтетических аккаунтов на защищённом локальном стенде.</p>
    <form onSubmit={event=>void submit(event)}>
      <label>Логин <input name="login" autoComplete="username" value={login}
        onChange={event=>setLogin(event.target.value)} required minLength={3}/></label>
      <label>Пароль <input name="password" type="password" autoComplete="current-password"
        value={password} onChange={event=>setPassword(event.target.value)} required/></label>
      <button type="submit" disabled={busy}>{busy?'Проверка…':'Войти'}</button>
    </form>
    {status && <p role="alert">{status}</p>}
  </section>;
}

/** Never accept a subject from storage, request headers or local mock data. */
export function Account() {
  const [state, setState] = React.useState<SessionState>('loading');
  const [refresh, setRefresh] = React.useState(0);
  const [csrf, setCsrf] = React.useState<string|null>(null);
  const [logoutError,setLogoutError]=React.useState('');
  React.useEffect(() => {
    const controller = new AbortController();
    async function inspect() {
      setState('loading');
      try {
        const response = await fetch('/api/v1/auth/me', {
          method: 'GET',
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (controller.signal.aborted) return;
        if (response.status === 401 || response.status === 403) {
          setState('anonymous');
          return;
        }
        if (!response.ok) {
          setState('unavailable');
          return;
        }
        const json: unknown = await response.json();
        if (json && typeof json === 'object' && 'subjectId' in json &&
            typeof json.subjectId === 'string' &&
            /^[0-9a-f-]{36}$/i.test(json.subjectId)) {
          setState('authenticated');
        } else {
          setState('unavailable');
        }
      } catch {
        if (!controller.signal.aborted) setState('unavailable');
      }
    }
    void inspect();
    return () => controller.abort();
  }, [refresh]);

  if (state === 'loading') return <section aria-live="polite"><h1>Личный кабинет</h1><p role="status">Проверка серверной сессии…</p></section>;
  if (state === 'anonymous') return <section><h1>Личный кабинет</h1>
    <Routes>
      <Route path="login" element={<LocalLogin onSuccess={value=>{
        setCsrf(value);setRefresh(x=>x+1);
      }}/>} />
      <Route path="*" element={<>
        <p role="status">401 — требуется локальная авторизация. Войдите, чтобы продолжить.</p>
        <Link to="/account/login">Локальный вход</Link> · <Link to="/">На главную</Link>
      </>}/>
    </Routes></section>;
  if (state === 'unavailable') return <section><h1>Личный кабинет</h1>
    <p role="alert">Не удалось проверить серверную сессию. Данные кабинета не показаны.</p>
    <button type="button" onClick={() => setRefresh(count => count + 1)}>Повторить проверку</button>
    </section>;
  async function logout() {
    if (!csrf) {setLogoutError('Для завершения сессии после обновления страницы войдите повторно.');return;}
    setLogoutError('');
    try {
      const response=await fetch('/api/v1/auth/logout',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'X-CSRF-Token':csrf},
      });
      if (!response.ok) {
        setLogoutError('Не удалось завершить сессию. Попробуйте снова.');
        return;
      }
      setCsrf(null);setState('anonymous');
    } catch {setLogoutError('Нет связи с сервером.');}
  }
  return <div className="account-shell">
    <h1>Личный кабинет</h1>
    <button type="button" onClick={()=>void logout()}>Выйти</button>
    {logoutError && <p role="alert">{logoutError}</p>}
    <p>Доступ подтверждён сервером. Разделы показывают данные только после отдельной проверки прав.</p>
    <nav className="account-nav" aria-label="Разделы личного кабинета">
      {sections.map(item => <NavLink key={item.id}
        to={'/account/' + item.id}>{item.label}</NavLink>)}
    </nav>
    <Routes>
      <Route index element={<Navigate to="overview" replace />} />
      <Route path="login" element={<Navigate to="/account/overview" replace />} />
      {sections.map(item => <Route key={item.id} path={item.id} element={<Section id={item.id} csrf={csrf} />} />)}
      <Route path="*" element={<section><h2>Раздел не найден</h2><Link to="/account/overview">К обзору</Link></section>} />
    </Routes>
  </div>;
}
