import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const outcomes=['ACCEPTED','TIMEOUT','RATE_LIMIT','REJECTED'] as const;
type Outcome=typeof outcomes[number];
const names:Record<Outcome,string>={ACCEPTED:'Тестовый приём',TIMEOUT:'Тайм-аут',RATE_LIMIT:'Лимит провайдера',REJECTED:'Окончательный отказ'};
const statuses={READY:'Нет попыток',RETRY_AVAILABLE:'Можно повторить',MOCK_ACCEPTED:'Принято тестовым адаптером',MOCK_REJECTED:'Окончательный отказ',EXHAUSTED:'Лимит попыток исчерпан',SUPPRESSED:'Согласие отозвано или изменено'} as const;
type Status=keyof typeof statuses;
type State={syntheticOnly:true;sendAllowed:false;realDelivery:false;messageId:string;channelCode:string;providerCode:string;status:Status;
  attempts:{id:string;ordinal:number;outcome:Outcome;createdAt:string}[];replayed?:boolean;attemptId?:string;attemptOrdinal?:number};
type Pending={requestKey:string;outcome:Outcome};
const stamp=(x:unknown)=>typeof x==='string'&&Number.isFinite(Date.parse(x));
function parse(raw:unknown,message:string,channel:string):State{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as State;
  if(x.syntheticOnly!==true||x.sendAllowed!==false||x.realDelivery!==false||x.messageId!==message||
    x.channelCode!==channel||x.providerCode!=='FIXTURE_'+channel||
    !Object.prototype.hasOwnProperty.call(statuses,x.status)||!Array.isArray(x.attempts)||x.attempts.length>3||
    !x.attempts.every((a,i)=>a&&uuid.test(a.id)&&a.ordinal===i+1&&outcomes.includes(a.outcome)&&stamp(a.createdAt)&&
      (i===x.attempts.length-1||['TIMEOUT','RATE_LIMIT'].includes(a.outcome)))||
    new Set(x.attempts.map(a=>a.id)).size!==x.attempts.length)throw Error('shape');
  const last=x.attempts[x.attempts.length-1]?.outcome;
  const expected=last==='ACCEPTED'?'MOCK_ACCEPTED':last==='REJECTED'?'MOCK_REJECTED':
    x.attempts.length===3?'EXHAUSTED':last?'RETRY_AVAILABLE':'READY';
  if(x.status!=='SUPPRESSED'&&x.status!==expected)throw Error('status');
  return x;
}
export function ProviderAttempts({path,message,channel,onDenied}:{path:string;message:string;channel:string;onDenied:()=>void}){
  const [state,setState]=React.useState<State|null>(null),[pending,setPending]=React.useState<Pending|null>(null);
  const [outcome,setOutcome]=React.useState<Outcome>('ACCEPTED'),[busy,setBusy]=React.useState(false),[note,setNote]=React.useState('');
  const request=React.useRef<AbortController|null>(null);
  const denied=React.useRef(onDenied);denied.current=onDenied;
  React.useEffect(()=>{void run();return ()=>{request.current?.abort();request.current=null;};},[path]);
  async function run(body?:Pending){
    if(request.current)return;
    const c=new AbortController();request.current=c;setBusy(true);setNote('');setState(null);
    try{
      let csrf:string|undefined;
      if(body){
        setPending(body);
        const res=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal:c.signal});
        if(c.signal.aborted)return;
        if([401,403,404].includes(res.status)){setPending(null);denied.current();return;}
        if(!res.ok)throw Error('csrf');
        csrf=(await res.json()).csrfToken;
        if(typeof csrf!=='string'||!csrf)throw Error('csrf');
      }
      if(c.signal.aborted)return;
      const res=await fetch(path,{credentials:'include',cache:'no-store',signal:c.signal,
        ...(body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf!},body:JSON.stringify(body)}:{})});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){setPending(null);denied.current();return;}
      if(res.status===409||res.status===422){setPending(null);setNote('Повтор недоступен: согласие изменилось, лимит или конфликт. Обновите историю.');return;}
      if(!res.ok)throw Error('http');
      const data=parse(await res.json(),message,channel);
      if(c.signal.aborted)return;
      if(body&&(data.replayed!==(res.status===200)||data.status==='SUPPRESSED'||
        !data.attempts.some(a=>a.id===data.attemptId&&a.ordinal===data.attemptOrdinal&&a.outcome===body.outcome)))throw Error('receipt');
      setState(data);setPending(null);
    }catch{if(!c.signal.aborted)setNote(body?'Ответ неизвестен. Повторите тот же запрос.':'Не удалось прочитать попытки.');}
    finally{if(request.current===c){request.current=null;if(!c.signal.aborted)setBusy(false);}}
  }
  return <section aria-label="История тестовых попыток" style={{overflowWrap:'anywhere'}}>
    <p>Только fixture, не факт доставки. Не более трёх попыток; повтор только после тайм-аута или лимита.</p>
    <button type="button" disabled={busy||!!pending} onClick={()=>void run()}>Обновить попытки</button>
    {state&&<><p>{statuses[state.status]}</p><ol>{state.attempts.map(a=><li key={a.id}>{a.ordinal}. {names[a.outcome]} · {new Date(a.createdAt).toLocaleString('ru-RU')}</li>)}</ol>
      {['READY','RETRY_AVAILABLE'].includes(state.status)&&<><label>Сценарий адаптера <select aria-label="Сценарий адаптера"
        value={outcome} disabled={busy||!!pending} onChange={e=>setOutcome(e.target.value as Outcome)}>
        {outcomes.map(o=><option key={o} value={o}>{names[o]}</option>)}</select></label>
        <button type="button" disabled={busy||!!pending} onClick={()=>void run({requestKey:crypto.randomUUID(),outcome})}>Выполнить тестовую попытку</button></>}
    </>}
    {pending&&<button type="button" disabled={busy} onClick={()=>void run(pending)}>Повторить исходную попытку</button>}
    {note&&<p role="status">{note}</p>}
  </section>;
}

export function MockCallHistory({path,onDenied}:{path:string;onDenied:()=>void}){
  const [items,setItems]=React.useState<{id:string;disposition:'ANSWERED'|'MISSED';createdAt:string}[]|null>(null);
  const [busy,setBusy]=React.useState(false),[note,setNote]=React.useState('');
  const [next,setNext]=React.useState<string|null>(null);
  const request=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>request.current?.abort(),[]);
  async function load(cursor:string|null=null){
    if(busy)return;request.current?.abort();const c=new AbortController();request.current=c;
    setItems(null);setNext(null);setBusy(true);setNote('');
    try{
      const res=await fetch(path+(cursor?'?cursor='+encodeURIComponent(cursor):''),{credentials:'include',cache:'no-store',signal:c.signal});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){onDenied();return;}
      if(!res.ok)throw Error('http');
      const raw=await res.json();
      if(raw.syntheticOnly!==true||raw.realCall!==false||!Array.isArray(raw.items)||raw.items.length>50||!(raw.nextCursor===null||(typeof raw.nextCursor==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(raw.nextCursor)&&raw.nextCursor!==cursor&&raw.items.length===50))||
        !raw.items.every((x:{id:string;disposition:string;createdAt:string})=>x&&uuid.test(x.id)&&
          ['ANSWERED','MISSED'].includes(x.disposition)&&stamp(x.createdAt)))throw Error('shape');
      if(!c.signal.aborted){setItems(raw.items);setNext(raw.nextCursor);}
    }catch{if(!c.signal.aborted)setNote('История звонков недоступна.');}
    finally{if(!c.signal.aborted)setBusy(false);}
  }
  return <section aria-label="История тестовых звонков"><h3>Тестовые звонки</h3>
    <button type="button" disabled={busy} onClick={()=>void load()}>Показать журнал звонков</button>
    {next&&<button type="button" disabled={busy} onClick={()=>void load(next)}>Следующая страница звонков</button>}
    {items&&(items.length?<ul>{items.map(x=><li key={x.id}>{x.disposition==='MISSED'?'Пропущен':'Принят'} · {new Date(x.createdAt).toLocaleString('ru-RU')}</li>)}</ul>:<p>Звонков нет.</p>)}
    {note&&<p role="status">{note}</p>}
  </section>;
}
