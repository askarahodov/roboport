import React from 'react';

type Channel='CALL'|'EMAIL'|'SMS';
type Template='BOOKING_REMINDER'|'SERVICE_UPDATE'|'POST_SERVICE_REVIEW';
type Consent={syntheticOnly:true;purposeCode:'SERVICE_UPDATE';
  legalConsentVerified:false;sendAllowed:false;channels:Record<Channel,boolean>};
type Item={messageId:string;channelCode:Channel;templateCode:Template;
  status:'MOCK_ONLY'|'SUPPRESSED';createdAt:string};
type Snapshot={syntheticOnly:true;sendAllowed:false;legalConsentVerified:false;items:Item[]};
type Pending={channelCode:Channel;templateCode:Template;requestKey:string};
const channels:Channel[]=['CALL','EMAIL','SMS'];
const templates:Template[]=['BOOKING_REMINDER','SERVICE_UPDATE','POST_SERVICE_REVIEW'];
const channelNames:Record<Channel,string>={CALL:'Звонок',EMAIL:'Почта',SMS:'SMS'};
const templateNames:Record<Template,string>={
  BOOKING_REMINDER:'Напоминание о записи',
  SERVICE_UPDATE:'Статус обслуживания',
  POST_SERVICE_REVIEW:'Контроль после ремонта',
};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function parseConsent(raw:unknown):Consent{
  if(!raw||typeof raw!=='object')throw Error('consent');
  const x=raw as Partial<Consent>;
  if(x.syntheticOnly!==true||x.purposeCode!=='SERVICE_UPDATE'||
    x.legalConsentVerified!==false||x.sendAllowed!==false||!x.channels||
    channels.some(c=>typeof x.channels?.[c]!=='boolean'))throw Error('consent');
  return x as Consent;
}
function parseMessages(raw:unknown):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('messages');
  const x=raw as Partial<Snapshot>;
  if(x.syntheticOnly!==true||x.sendAllowed!==false||
    x.legalConsentVerified!==false||!Array.isArray(x.items)||x.items.length>100||
    !x.items.every(item=>item&&uuid.test(item.messageId)&&
      channels.includes(item.channelCode)&&templates.includes(item.templateCode)&&
      ['MOCK_ONLY','SUPPRESSED'].includes(item.status)&&
      typeof item.createdAt==='string'&&!Number.isNaN(Date.parse(item.createdAt))))
    throw Error('messages');
  return x as Snapshot;
}

export function StaffCrmMockMessages({base,leadId,onDenied}:{
  base:string;leadId:string;onDenied:()=>void;
}){
  const [consent,setConsent]=React.useState<Consent|null>(null);
  const [messages,setMessages]=React.useState<Item[]>([]);
  const [channel,setChannel]=React.useState<Channel>('CALL');
  const [template,setTemplate]=React.useState<Template>('SERVICE_UPDATE');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [revision,setRevision]=React.useState(0);
  const controller=React.useRef<AbortController|null>(null);
  React.useEffect(()=>{setConsent(null);setMessages([]);setStatus('idle');setMessage('');setPending(null);},
    [base,leadId]);
  React.useEffect(()=>()=>controller.current?.abort(),[]);

  async function load(){
    controller.current?.abort();
    const current=new AbortController();controller.current=current;
    setStatus('loading');setConsent(null);setMessages([]);
    try{
      const prefix=base+'/'+encodeURIComponent(leadId);
      const [c,m]=await Promise.all([
        fetch(prefix+'/contact-consent',{credentials:'include',cache:'no-store',signal:current.signal}),
        fetch(prefix+'/mock-messages',{credentials:'include',cache:'no-store',signal:current.signal}),
      ]);
      if(current.signal.aborted)return;
      if([401,403,404].includes(c.status)||[401,403,404].includes(m.status)){
        setStatus('denied');onDenied();return;
      }
      if(!c.ok||!m.ok)throw Error('http');
      const [cs,ms]=await Promise.all([c.json(),m.json()]);
      const parsedConsent=parseConsent(cs),parsedMessages=parseMessages(ms);
      if(!current.signal.aborted){setConsent(parsedConsent);setMessages(parsedMessages.items);setStatus('ready');}
    }catch{if(!current.signal.aborted){setConsent(null);setMessages([]);setStatus('error');}}
  }

  async function submit(item:Pending){
    if(busy||status!=='ready')return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if([401,403,404].includes(csrf.status)){setStatus('denied');setConsent(null);setPending(null);onDenied();return;}
      if(!csrf.ok)throw Error('csrf');
      const value:unknown=await csrf.json();
      if(!value||typeof value!=='object'||!('csrfToken' in value)||
        typeof value.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(base+'/'+encodeURIComponent(leadId)+'/mock-messages',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken},
        body:JSON.stringify(item),
      });
      if(response.status===409){setPending(null);setMessage('Согласие отсутствует, изменилось или отозвано. Макет не создан.');await load();return;}
      if([401,403,404].includes(response.status)){setStatus('denied');setConsent(null);setPending(null);onDenied();return;}
      if(!response.ok)throw Error('http');
      const result:unknown=await response.json();
      if(!result||typeof result!=='object'||!('sendAllowed' in result)||
        result.sendAllowed!==false||!('status' in result)||result.status!=='MOCK_ONLY')
        throw Error('shape');
      setPending(null);
      setMessage('Добавлена только инертная запись MOCK_ONLY. Никакой отправки не было.');
      setRevision(n=>n+1);
    }catch{setMessage('Ответ неизвестен. Повторите макет с тем же ключом.');}
    finally{setBusy(false);}
  }

  function create(){
    if(busy||pending||status!=='ready'||!consent||!consent.channels[channel])return;
    const item:Pending={channelCode:channel,templateCode:template,
      requestKey:crypto.randomUUID()};
    setPending(item);void submit(item);
  }

  React.useEffect(()=>{if(revision>0)void load();},[revision]);
  return <section aria-label="Инертная очередь тестовых CRM уведомлений">
    <h3>Тестовые уведомления — без отправки</h3>
    <p>Макет outbox сохраняет только шаблон и канал. Нет текста, адресата,
      телефона, email, сетевого отправителя или фонового worker. sendAllowed=false всегда.</p>
    <button type="button" disabled={status==='loading'||busy} onClick={()=>void load()}>
      Проверить согласие и очередь</button>
    {status==='loading'&&<p role="status">Проверка…</p>}
    {status==='denied'&&<p role="status">Доступ запрещён. Данные очищены.</p>}
    {status==='error'&&<p role="alert">Не удалось прочитать очередь.</p>}
    {status==='ready'&&consent&&<>
      <p>Юридически проверенное согласие: нет. Реальная отправка: запрещена.</p>
      <ul>{channels.map(c=><li key={c}>{channelNames[c]}: {
        consent.channels[c]?'Тестовое согласие активно':'Согласие отсутствует или отозвано'
      }</li>)}</ul>
      <label>Канал <select aria-label="Канал тестового уведомления"
        value={channel} onChange={e=>setChannel(e.target.value as Channel)}>
        {channels.map(c=><option key={c} value={c}>{channelNames[c]}</option>)}
      </select></label>
      <label>Шаблон <select aria-label="Шаблон тестового уведомления"
        value={template} onChange={e=>setTemplate(e.target.value as Template)}>
        {templates.map(t=><option key={t} value={t}>{templateNames[t]}</option>)}
      </select></label>
      <button type="button" disabled={busy||pending!==null||!consent.channels[channel]}
        onClick={create}>Создать инертный макет</button>
      {pending&&<button type="button" disabled={busy}
        onClick={()=>void submit(pending)}>Повторить с тем же ключом</button>}
      <button type="button" disabled={busy} onClick={()=>setRevision(n=>n+1)}>
        Обновить после отзыва</button>
      <h4>Журнал макетов</h4>
      {messages.length===0?<p>Макетов нет.</p>:<ul>{messages.map(item=><li key={item.messageId}>
        {channelNames[item.channelCode]} · {templateNames[item.templateCode]} · {
          item.status==='MOCK_ONLY'?'Только тест':'Подавлено после отзыва'
        } · {new Date(item.createdAt).toLocaleString('ru-RU')}
      </li>)}</ul>}
    </>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
