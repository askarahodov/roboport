import React from 'react';

type Channel='CALL'|'EMAIL'|'SMS';
type Flags=Record<Channel,boolean>;
type Versions=Record<Channel,number>;
type Data={syntheticOnly:true;sendAllowed:false;channels:Flags;revisions:Versions};
type Pending={channelCode:Channel;enabled:boolean;expectedRevision:number;requestKey:string};
const keys:Channel[]=['CALL','EMAIL','SMS'];
const names:Record<Channel,string>={CALL:'Звонки',EMAIL:'Электронная почта',SMS:'SMS'};
const endpoint='/api/v1/dev/account/crm-contact-preferences';

function parse(raw:unknown):Data{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as Partial<Data>;
  if(x.syntheticOnly!==true||x.sendAllowed!==false||
     !x.channels||typeof x.channels!=='object'||
     !x.revisions||typeof x.revisions!=='object'||
     keys.some(k=>typeof x.channels?.[k]!=='boolean'||
       !Number.isSafeInteger(x.revisions?.[k])||
       (x.revisions?.[k]??-1)<0))throw Error('shape');
  return x as Data;
}

export function CustomerCrmContactPreferences(){
  const [data,setData]=React.useState<Data|null>(null);
  const [draft,setDraft]=React.useState<Flags>({CALL:false,EMAIL:false,SMS:false});
  const [status,setStatus]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [revision,setRevision]=React.useState(0);
  const [message,setMessage]=React.useState('');

  React.useEffect(()=>{
    const controller=new AbortController();
    setData(null);setDraft({CALL:false,EMAIL:false,SMS:false});
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
    if(busy||status!=='ready')return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if([401,403,404].includes(csrf.status)){
        setData(null);setStatus('denied');setPending(null);return;
      }
      if(!csrf.ok)throw Error('csrf');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
         typeof token.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(endpoint,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body:JSON.stringify(item),
      });
      if([401,403,404].includes(response.status)){
        setData(null);setStatus('denied');setPending(null);return;
      }
      if(response.status===409){
        setPending(null);
        setMessage('Предпочтения изменились. Обновите данные перед новым сохранением.');
        return;
      }
      if(!response.ok)throw Error('http');
      const result=parse(await response.json());
      setData(result);setDraft(result.channels);setPending(null);
      setMessage('Выбор записан в журнал. Отправка сообщений по-прежнему выключена.');
    }catch{
      setMessage('Ответ неизвестен. Используйте только повтор с прежним ключом.');
    }finally{setBusy(false);}
  }

  function save(channel:Channel){
    if(!data||pending||busy)return;
    const item:Pending={
      channelCode:channel,enabled:draft[channel],
      expectedRevision:data.revisions[channel],requestKey:crypto.randomUUID(),
    };
    setPending(item);
    void submit(item);
  }

  return <section aria-label="Предпочтения способов связи CRM (dev)">
    <h3>Способы связи — только тестовый контур</h3>
    <p>Вы можете указать предпочтение либо отказаться от звонков, почты и SMS.
      По умолчанию все способы отключены. Выбор записывается в неизменяемый журнал.
      Это не юридическое согласие на маркетинг и не разрешение отправлять сообщения:
      внешняя связь в dev-контуре отключена.</p>
    {status==='loading'&&<p role="status">Чтение предпочтений связи…</p>}
    {status==='denied'&&<p role="status">Нет доступа к предпочтениям связи.</p>}
    {status==='error'&&<p role="status">Предпочтения связи недоступны.
      <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить чтение</button></p>}
    {status==='ready'&&data&&<>
      <p>Реальная отправка: запрещена.</p>
      {keys.map(code=><div key={code}>
        <label><input type="checkbox" aria-label={names[code]}
          checked={draft[code]} disabled={busy||pending!==null}
          onChange={e=>setDraft(x=>({...x,[code]:e.target.checked}))}/>
          {names[code]}</label>
        <button type="button" disabled={busy||pending!==null}
          onClick={()=>save(code)}>Записать выбор: {names[code]}</button>
      </div>)}
      {pending&&<button type="button" disabled={busy}
        onClick={()=>void submit(pending)}>Повторить выбор с тем же ключом</button>}
      {message&&<p role="status">{message}</p>}
      <button type="button" disabled={busy||pending!==null}
        onClick={()=>setRevision(n=>n+1)}>Обновить предпочтения связи</button>
    </>}
  </section>;
}
