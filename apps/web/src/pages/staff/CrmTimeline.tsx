import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const kinds={CREATED:'Создание обращения',STAGE:'Этап',ASSIGNED:'Назначение',INBOUND:'Входящий контакт',
  TASK_CREATED:'Создание задачи',TASK_RESOLVED:'Завершение задачи',MOCK:'Тестовое сообщение'};
const codes:Record<string,string>={WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение',
  QUALIFIED:'Обработано',BOOKED:'Записан',LOST:'Отказ',ASSIGNED:'Назначен ответственный',
  REQUEST_RECEIVED:'Получено обращение',CALLBACK_REQUIRED:'Нужен обратный контакт',
  INFORMATION_REQUESTED:'Запрошена информация',DECLINED:'Отказ от контакта',
  CALL_BACK:'Обратный контакт',CHECK_BOOKING:'Проверка записи',SERVICE_REVIEW:'Оценка сервиса',
  DONE:'Выполнено',CANCELED:'Отменено',BOOKING_REMINDER:'Напоминание о записи',
  SERVICE_UPDATE:'Информация о сервисе',POST_SERVICE_REVIEW:'Оценка после сервиса'};
const allowed:Record<keyof typeof kinds,string[]>={CREATED:['WEB','PHONE','WALK_IN'],STAGE:['QUALIFIED','BOOKED','LOST'],
  ASSIGNED:['ASSIGNED'],INBOUND:['REQUEST_RECEIVED','CALLBACK_REQUIRED','INFORMATION_REQUESTED','DECLINED'],
  TASK_CREATED:['CALL_BACK','CHECK_BOOKING','SERVICE_REVIEW'],TASK_RESOLVED:['DONE','CANCELED'],
  MOCK:['BOOKING_REMINDER','SERVICE_UPDATE','POST_SERVICE_REVIEW']};
const channels:Record<string,string>={CALL:'Звонок',EMAIL:'Почта',SMS:'SMS',MESSENGER:'Мессенджер',WALK_IN:'Личное обращение'};
type Row={kind:keyof typeof kinds;eventId:string;createdAt:string;actorId:string;code:string;
  relatedId:string|null;channelCode:string|null;status:'MOCK_ONLY'|'SUPPRESSED'|null};
type Page={items:Row[];nextCursor:string|null};
function parse(value:unknown,leadId:string):Page{
  if(!value||typeof value!=='object')throw Error('page');
  const p=value as Page&{syntheticOnly:boolean;sendAllowed:boolean;legalConsentVerified:boolean;leadId:string};
  if(p.syntheticOnly!==true||p.sendAllowed!==false||p.legalConsentVerified!==false||p.leadId!==leadId||
    !Array.isArray(p.items)||p.items.length>25||
    !(p.nextCursor===null||typeof p.nextCursor==='string'&&/^[0-6]\.[A-Za-z0-9_-]{1,254}$/.test(p.nextCursor)&&p.items.length===25))throw Error('page');
  if(!p.items.every(x=>x&&Object.hasOwn(kinds,x.kind)&&allowed[x.kind].includes(x.code)&&
    typeof x.eventId==='string'&&uuid.test(x.eventId)&&typeof x.actorId==='string'&&uuid.test(x.actorId)&&
    typeof x.createdAt==='string'&&Number.isFinite(Date.parse(x.createdAt))&&
    (x.relatedId===null||typeof x.relatedId==='string'&&uuid.test(x.relatedId))&&
    (x.kind==='MOCK'?['MOCK_ONLY','SUPPRESSED'].includes(x.status??'')&&['CALL','EMAIL','SMS','MESSENGER'].includes(x.channelCode??''):
      x.status===null&&(x.kind==='INBOUND'?Object.hasOwn(channels,x.channelCode??''):x.channelCode===null)))||
    new Set(p.items.map(x=>x.kind+':'+x.eventId)).size!==p.items.length)throw Error('rows');
  return p;
}
export function CrmTimeline({base,leadId,onDenied}:{base:string;leadId:string;onDenied?:()=>void}){
  const [data,setData]=React.useState<Page|null>(null),[message,setMessage]=React.useState(''),[busy,setBusy]=React.useState(false);
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  async function load(cursor:string|null=null){
    active.current?.abort();const control=new AbortController();active.current=control;
    setData(null);setMessage('');setBusy(true);
    try{
      const response=await fetch(base+'/'+encodeURIComponent(leadId)+'/timeline'+(cursor?'?cursor='+encodeURIComponent(cursor):''),
        {credentials:'include',cache:'no-store',signal:control.signal});
      if(control.signal.aborted)return;
      if([401,403,404].includes(response.status)){setMessage('Доступ к хронологии запрещён.');onDenied?.();return;}
      if(!response.ok)throw Error('http');
      const result=parse(await response.json(),leadId);
      if(control.signal.aborted)return;
      if(cursor&&result.nextCursor===cursor)throw Error('cursor');
      setData(result);
    }catch{if(!control.signal.aborted)setMessage('Хронология недоступна. Повторите с первой страницы.');}
    finally{if(!control.signal.aborted)setBusy(false);}
  }
  return <section aria-label="Единая хронология сделки">
    <h3>Единая хронология сделки</h3>
    <p>Искусственная история без отправки сообщений. Статус макета учитывает действующее согласие.</p>
    <button type="button" disabled={busy} onClick={()=>void load()}>{data?'Обновить хронологию':'Загрузить хронологию'}</button>
    {busy&&<p role="status">Загрузка хронологии…</p>}
    {message&&<p role="alert">{message}</p>}
    {data&&<>{data.items.length===0?<p>Событий нет.</p>:<ol>{data.items.map(x=><li key={x.kind+':'+x.eventId}>
      <strong>{kinds[x.kind]}: {codes[x.code]}</strong> · {new Date(x.createdAt).toLocaleString('ru-RU')}
      {x.channelCode&&' · '+channels[x.channelCode]}{x.status&&' · '+(x.status==='SUPPRESSED'?'Подавлено':'Только макет')}
      <details><summary>Аудит события</summary><p style={{overflowWrap:'anywhere'}}>UUID: {x.eventId}; сотрудник: {x.actorId}{x.relatedId&&'; связь: '+x.relatedId}</p></details>
    </li>)}</ol>}
    {data.nextCursor&&<button type="button" disabled={busy} onClick={()=>void load(data.nextCursor)}>Следующая страница хронологии</button>}</>}
  </section>;
}
