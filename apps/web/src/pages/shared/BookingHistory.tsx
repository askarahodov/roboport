import React from 'react';

type Receipt={bookingId:string;bayCode:string;startsAt:string;endsAt:string;recordedAt:string;
  action:'CREATED'|'RESCHEDULED';state:'CURRENT'|'REPLACED'|'CANCELED';
  notificationStatus:string|null;attempts:number|null};
type Cancellation={recordedAt:string;notificationStatus:string|null;attempts:number|null};
type History={appointmentId:string;workOrderId:string;timeZone:string;items:Receipt[];
  cancellation:Cancellation|null;nextCursor:string|null;syntheticOnly:true;
  deliveryChannel:'DEV_SINK';realDelivery:false};
const statuses:Record<string,string>={PENDING:'ожидает',PROCESSING:'обрабатывается',
  DELIVERED:'записано в тестовый приёмник',DEAD:'исчерпаны попытки',SUPERSEDED:'заменено новым событием'};
const notification=(state:string|null,attempts:number|null)=>
  (state===null?'событие отсутствует':statuses[state]??'неизвестное состояние')+
  (attempts!==null?' · попыток: '+attempts:'');
const validTime=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v));
const validNotice=(x:{notificationStatus:string|null;attempts:number|null})=>
  (x.notificationStatus===null||Object.prototype.hasOwnProperty.call(statuses,x.notificationStatus))&&
  (x.attempts===null||(Number.isInteger(x.attempts)&&x.attempts>=0&&x.attempts<=5));

export function BookingHistory({path,appointment,onDenied}:{path:string;appointment:string;onDenied:()=>void}){
  const [data,setData]=React.useState<History|null>(null),[cursor,setCursor]=React.useState<string|null>(null);
  const [status,setStatus]=React.useState(''),[refresh,setRefresh]=React.useState(0);
  const deny=React.useRef(onDenied);deny.current=onDenied;
  React.useEffect(()=>{
    const controller=new AbortController();setData(null);setStatus('Загрузка истории…');
    const url=path+(cursor?(path.includes('?')?'&':'?')+'cursor='+encodeURIComponent(cursor):'');
    void fetch(url,{credentials:'include',cache:'no-store',signal:controller.signal}).then(async res=>{
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){deny.current();return;}
      if(!res.ok)throw Error('history');
      const raw=await res.json() as History;
      if(!raw||raw.syntheticOnly!==true||raw.realDelivery!==false||raw.deliveryChannel!=='DEV_SINK'||
        raw.appointmentId!==appointment||typeof raw.workOrderId!=='string'||
        typeof raw.timeZone!=='string'||!Array.isArray(raw.items)||raw.items.length>50||
        !raw.items.every(x=>x&&typeof x.bookingId==='string'&&typeof x.bayCode==='string'&&
          validTime(x.startsAt)&&validTime(x.endsAt)&&validTime(x.recordedAt)&&
          ['CREATED','RESCHEDULED'].includes(x.action)&&['CURRENT','REPLACED','CANCELED'].includes(x.state)&&
          validNotice(x))||!(raw.cancellation===null||(raw.cancellation&&
          validTime(raw.cancellation.recordedAt)&&validNotice(raw.cancellation)))||
        !(raw.nextCursor===null||(typeof raw.nextCursor==='string'&&
          /^[A-Za-z0-9_-]{1,256}$/.test(raw.nextCursor)&&raw.nextCursor!==cursor&&raw.items.length>0)))
        throw Error('shape');
      new Intl.DateTimeFormat('ru-RU',{timeZone:raw.timeZone});
      if(!controller.signal.aborted){setData(raw);setStatus('');}
    }).catch(()=>{if(!controller.signal.aborted)setStatus('Не удалось загрузить историю.');});
    return ()=>controller.abort();
  },[path,appointment,cursor,refresh]);
  const local=(stamp:string)=>new Intl.DateTimeFormat('ru-RU',{timeZone:data?.timeZone,
    dateStyle:'short',timeStyle:'short'}).format(new Date(stamp));
  return <section aria-label="История бронирования" style={{overflowWrap:'anywhere'}}>
    <h4>История бронирования</h4>
    <p>Уведомления только в DEV_SINK, реальной отправки нет.</p>
    {status&&<p role="status">{status}</p>}
    {data&&<>
      <p>Заказ-наряд: {data.workOrderId}</p>
      <ul>{data.items.map(x=><li key={x.bookingId}>
        {x.action==='CREATED'?'Создана':'Перенесена'} · {x.state==='CURRENT'?'Действует':
          x.state==='REPLACED'?'Заменена':'Отменена'} · пост {x.bayCode}
        <p>{local(x.startsAt)} — {local(x.endsAt)} ({data.timeZone})</p>
        <p>Уведомление: {notification(x.notificationStatus,x.attempts)}</p>
      </li>)}</ul>
      {data.cancellation&&<p>Отмена: {local(data.cancellation.recordedAt)} ·
        уведомление: {notification(data.cancellation.notificationStatus,data.cancellation.attempts)}</p>}
      {data.nextCursor&&<button type="button" onClick={()=>setCursor(data.nextCursor)}>Более ранние записи истории</button>}
    </>}
    <button type="button" onClick={()=>{setCursor(null);setRefresh(n=>n+1);}}>Обновить историю</button>
  </section>;
}
