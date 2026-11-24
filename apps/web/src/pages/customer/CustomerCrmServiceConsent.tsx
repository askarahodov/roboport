import React from 'react';

type Channel='CALL'|'EMAIL'|'SMS'|'MESSENGER';
type Flags=Record<Channel,boolean>;
type Revisions=Record<Channel,number>;
type Snapshot={syntheticOnly:true;purposeCode:'SERVICE_UPDATE';
  legalConsentVerified:false;sendAllowed:false;channels:Flags;revisions:Revisions};
type Pending={channelCode:Channel;enabled:boolean;expectedRevision:number;requestKey:string};
const channels:Channel[]=['CALL','EMAIL','SMS','MESSENGER'];
const names:Record<Channel,string>={CALL:'Звонок',EMAIL:'Электронная почта',SMS:'SMS',MESSENGER:'Мессенджер'};
const endpoint='/api/v1/dev/account/crm-provider-consent';

function parse(raw:unknown):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as Partial<Snapshot>;
  if(x.syntheticOnly!==true||x.purposeCode!=='SERVICE_UPDATE'||
     x.legalConsentVerified!==false||x.sendAllowed!==false||
     !x.channels||!x.revisions||
     channels.some(c=>typeof x.channels?.[c]!=='boolean'||
       !Number.isSafeInteger(x.revisions?.[c])||(x.revisions?.[c]??-1)<0))
    throw Error('shape');
  return x as Snapshot;
}

export function CustomerCrmServiceConsent(){
  const [data,setData]=React.useState<Snapshot|null>(null);
  const [draft,setDraft]=React.useState<Flags>({CALL:false,EMAIL:false,SMS:false,MESSENGER:false});
  const [status,setStatus]=React.useState<'loading'|'ready'|'denied'|'error'|'conflict'>('loading');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [revision,setRevision]=React.useState(0);
  const [message,setMessage]=React.useState('');
  const mutation=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>mutation.current?.abort(),[]);
  React.useEffect(()=>{
    const controller=new AbortController();
    setData(null);setDraft({CALL:false,EMAIL:false,SMS:false,MESSENGER:false});
    setPending(null);setMessage('');setStatus('loading');
    void (async()=>{
      try{
        const response=await fetch(endpoint,{credentials:'include',cache:'no-store',
          signal:controller.signal});
        if(controller.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw Error('http');
        const result=parse(await response.json());
        if(!controller.signal.aborted){setData(result);setDraft(result.channels);setStatus('ready');}
      }catch{if(!controller.signal.aborted)setStatus('error');}
    })();
    return ()=>controller.abort();
  },[revision]);

  async function submit(item:Pending){
    if(busy||status!=='ready'||mutation.current)return;
    const controller=new AbortController();mutation.current=controller;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(csrf.status)){setData(null);setStatus('denied');setPending(null);return;}
      if(!csrf.ok)throw Error('csrf');
      const value:unknown=await csrf.json();
      if(controller.signal.aborted)return;
      if(!value||typeof value!=='object'||!('csrfToken' in value)||
         typeof value.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(endpoint,{method:'POST',credentials:'include',
        cache:'no-store',headers:{'Content-Type':'application/json',
          'X-CSRF-Token':value.csrfToken},body:JSON.stringify(item),signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setData(null);setStatus('denied');setPending(null);return;}
      if(response.status===409){
        // Устаревшая версия не является подтверждённым состоянием.
        setData(null);setDraft({CALL:false,EMAIL:false,SMS:false,MESSENGER:false});
        setPending(null);setStatus('conflict');return;
      }
      if(!response.ok)throw Error('http');
      const raw:unknown=await response.json();
      if(controller.signal.aborted)return;
      const result=parse(raw);
      const replayed=(raw as {replayed?:unknown}).replayed;
      const current=result.revisions[item.channelCode];
      // Новый ответ обязан подтвердить точную запись; повтор может вернуть
      // более свежий журнал после изменения другим сеансом.
      if((response.status!==201||replayed!==false)&&(response.status!==200||replayed!==true))
        throw Error('receipt');
      if(current<item.expectedRevision+1||
        (replayed===false&&current!==item.expectedRevision+1)||
        (current===item.expectedRevision+1&&result.channels[item.channelCode]!==item.enabled))
        throw Error('receipt');
      setData(result);setDraft(result.channels);setPending(null);
      setMessage(current>item.expectedRevision+1?
        'Запрос уже обработан, но выбор позднее изменён. Показано текущее состояние.':
        'Тестовый выбор записан. Реальная отправка остаётся выключенной.');
    }catch{if(!controller.signal.aborted)setMessage('Ответ неизвестен. Повторите только с тем же ключом.');}
    finally{if(mutation.current===controller){mutation.current=null;if(!controller.signal.aborted)setBusy(false);}}
  }

  function save(channel:Channel){
    if(!data||pending||busy)return;
    const item:Pending={channelCode:channel,enabled:draft[channel],
      expectedRevision:data.revisions[channel],requestKey:crypto.randomUUID()};
    setPending(item);void submit(item);
  }

  return <section aria-label="Тестовое согласие на сервисные уведомления CRM">
    <h3>Согласие на тестовые сервисные уведомления</h3>
    <p>Отдельный синтетический журнал SERVICE_UPDATE. Это не юридически
      подтверждённое согласие и не согласие на маркетинг. Внешняя отправка,
      звонки и подключение реальных провайдеров запрещены. Только локальные тестовые адаптеры.</p>
    {status==='loading'&&<p role="status">Чтение тестового согласия…</p>}
    {status==='denied'&&<p role="status">Нет доступа к тестовому согласию.</p>}
    {status==='error'&&<p role="alert">Согласие недоступно.
      <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить</button></p>}
    {status==='conflict'&&<p role="alert">Согласие изменилось. Перечитайте текущее состояние перед новым выбором.
      <button type="button" onClick={()=>setRevision(n=>n+1)}>Обновить согласие</button></p>}
    {status==='ready'&&data&&<>
      <p>Юридическое подтверждение: отсутствует. Отправка: запрещена.</p>
      {channels.map(channel=><div key={channel}>
        <label><input type="checkbox" aria-label={names[channel]}
          checked={draft[channel]} disabled={busy||pending!==null}
          onChange={e=>setDraft(x=>({...x,[channel]:e.target.checked}))}/>
          {names[channel]}</label>
        <button type="button" disabled={busy||pending!==null}
          onClick={()=>save(channel)}>Сохранить тестовый выбор: {names[channel]}</button>
      </div>)}
      {pending&&<button type="button" disabled={busy}
        onClick={()=>void submit(pending)}>Повторить с тем же ключом</button>}
      {message&&<p role="status">{message}</p>}
      <button type="button" disabled={busy||pending!==null}
        onClick={()=>setRevision(n=>n+1)}>Обновить согласие</button>
    </>}
  </section>;
}
