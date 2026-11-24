import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const reasons=['QUALITY','TIMING','COMMUNICATION','NONE','OTHER'] as const;
type Reason=typeof reasons[number];
const labels:Record<Reason,string>={QUALITY:'Качество работ',TIMING:'Сроки',COMMUNICATION:'Общение',NONE:'Нет замечаний',OTHER:'Другое'};
const statuses={OPEN:'Ожидает ответа',ANSWERED:'Ответ получен',CANCELED:'Отменён',EXPIRED:'Срок истёк'} as const;
type Status=keyof typeof statuses;
type Survey={id:string;createdAt:string;expiresAt:string;status:Status;rating:number|null;
  reasonCode:Reason|null;resolvedAt:string|null};
type State={syntheticOnly:true;sendAllowed:false;releaseAllowed:false;workOrderId:string|null;eligible:boolean;survey:Survey|null;replayed?:boolean};
type Pending={requestKey:string;action:'INVITE'|'CANCEL'}|{requestKey:string;rating:number;reasonCode:Reason};
const time=(s:unknown)=>typeof s==='string'&&Number.isFinite(Date.parse(s));
function parse(raw:unknown,order?:string):State{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as State;
  if(x.syntheticOnly!==true||x.sendAllowed!==false||x.releaseAllowed!==false||
    typeof x.eligible!=='boolean'||!(x.workOrderId===null||uuid.test(x.workOrderId))||
    (order&&x.workOrderId!==order))throw Error('shape');
  if(x.survey!==null){
    const s=x.survey;
    if(!s||!x.eligible||!x.workOrderId||!uuid.test(s.id)||!time(s.createdAt)||!time(s.expiresAt)||
      Date.parse(s.expiresAt)-Date.parse(s.createdAt)!==30*86400000||
      !Object.prototype.hasOwnProperty.call(statuses,s.status)||
      (s.status==='ANSWERED'?(s.rating===null||!Number.isInteger(s.rating)||s.rating<1||s.rating>5||
        s.reasonCode===null||!reasons.includes(s.reasonCode)):(s.rating!==null||s.reasonCode!==null))||
      (s.status==='ANSWERED'||s.status==='CANCELED'?!time(s.resolvedAt):s.resolvedAt!==null))throw Error('survey');
  }
  return x;
}
export function ServiceSurvey({customer=false,path,order,onDenied}:{customer?:boolean;path:string;order?:string;onDenied:()=>void}){
  const [open,setOpen]=React.useState(false),[state,setState]=React.useState<State|null>(null);
  const [message,setMessage]=React.useState(''),[busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [rating,setRating]=React.useState(5),[reason,setReason]=React.useState<Reason>('NONE');
  const request=React.useRef<AbortController|null>(null),locked=React.useRef(false);
  React.useEffect(()=>()=>request.current?.abort(),[]);
  async function execute(body?:Pending){
    if(locked.current)return;
    locked.current=true;setBusy(true);setOpen(true);setMessage('');
    const c=new AbortController();request.current=c;
    try{
      let csrf:string|undefined;
      if(body){
        setPending(body);
        const response=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal:c.signal});
        if(c.signal.aborted)return;
        if([401,403,404].includes(response.status)){setState(null);setPending(null);onDenied();return;}
        if(!response.ok)throw Error('csrf');
        csrf=(await response.json()).csrfToken;
        if(typeof csrf!=='string'||!csrf)throw Error('csrf');
      }
      if(c.signal.aborted)return;
      const res=await fetch(path,{credentials:'include',cache:'no-store',signal:c.signal,
        ...(body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf!},body:JSON.stringify(body)}:{})});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){setState(null);setPending(null);onDenied();return;}
      if(res.status===409||res.status===422){setState(null);setPending(null);setMessage('Конфликт или неверные данные. Обновите опрос.');return;}
      if(!res.ok)throw Error('http');
      const data=parse(await res.json(),order);
      if(c.signal.aborted)return;
      if(body){
        if(data.replayed!==(res.status===200)||!data.survey||
          ('action' in body?(body.action==='CANCEL'&&data.survey.status!=='CANCELED'):
          (data.survey.status!=='ANSWERED'||data.survey.rating!==body.rating||data.survey.reasonCode!==body.reasonCode)))
          throw Error('receipt');
      }
      setState(data);setPending(null);setMessage(body?'Действие подтверждено сервером.':'');
    }catch{if(!c.signal.aborted){setState(null);setMessage(body?'Результат неизвестен. Повторите исходное действие.':'Не удалось получить опрос.');}}
    finally{if(!c.signal.aborted){locked.current=false;setBusy(false);}}
  }
  const survey=state?.survey;
  return <section aria-label="Опрос после ремонта" style={{minWidth:0,overflowWrap:'anywhere'}}>
    <h3>Опрос после ремонта</h3>
    <p>Тестовая анкета внутри кабинета. Сообщения и уведомления не отправляются.</p>
    <button type="button" disabled={busy||!!pending} onClick={()=>void execute()}>{open?'Обновить опрос':'Открыть опрос'}</button>
    {state&&(!state.survey?<p>{customer?'Доступного опроса нет.':state.eligible?'Опрос ещё не создан.':'Нужен технически закрытый заказ.'}</p>:
      <><p>{statuses[survey!.status]}</p>
        <p>Срок ответа: {new Date(survey!.expiresAt).toLocaleString('ru-RU')}</p>
        {survey!.status==='ANSWERED'&&<p>Оценка: {survey!.rating} из 5. Причина: {labels[survey!.reasonCode!]}.</p>}
      </>)}
    {!customer&&state?.eligible&&!survey&&<button type="button" disabled={busy||!!pending}
      onClick={()=>void execute({requestKey:crypto.randomUUID(),action:'INVITE'})}>Создать опрос в кабинете</button>}
    {!customer&&survey?.status==='OPEN'&&<button type="button" disabled={busy||!!pending}
      onClick={()=>void execute({requestKey:crypto.randomUUID(),action:'CANCEL'})}>Отменить опрос</button>}
    {customer&&survey?.status==='OPEN'&&<fieldset disabled={busy||!!pending} style={{minInlineSize:0}}>
      <legend>Оцените обслуживание</legend>
      <label>Оценка <select value={rating} onChange={e=>setRating(Number(e.target.value))}>
        {[1,2,3,4,5].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
      <label>Причина <select value={reason} onChange={e=>setReason(e.target.value as Reason)}>
        {reasons.map(n=><option key={n} value={n}>{labels[n]}</option>)}</select></label>
      <button type="button" onClick={()=>void execute({requestKey:crypto.randomUUID(),rating,reasonCode:reason})}>Подтвердить оценку</button>
    </fieldset>}
    {pending&&<button type="button" disabled={busy} onClick={()=>void execute(pending)}>Повторить исходное действие</button>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
type Report={syntheticOnly:true;sendAllowed:false;releaseAllowed:false;fromDay:string;toDay:string;
 timeZone:string;total:number;statuses:Record<Status,number>;ratings:Record<string,number>;
 reasons:Record<Reason,number>;averageHundredths:number|null};
const count=(v:unknown)=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<=1000;
function validReport(x:Report,from:string,to:string){
  const keys=Object.keys(statuses) as Status[],ratings=['1','2','3','4','5'];
  return x&&x.syntheticOnly===true&&x.sendAllowed===false&&x.releaseAllowed===false&&
    x.fromDay===from&&x.toDay===to&&typeof x.timeZone==='string'&&count(x.total)&&
    x.statuses&&x.ratings&&x.reasons&&keys.every(k=>count(x.statuses[k]))&&
    ratings.every(k=>count(x.ratings[k]))&&reasons.every(k=>count(x.reasons[k]))&&
    keys.reduce((sum,k)=>sum+x.statuses[k],0)===x.total&&
    ratings.reduce((sum,k)=>sum+x.ratings[k],0)===x.statuses.ANSWERED&&
    reasons.reduce((sum,k)=>sum+x.reasons[k],0)===x.statuses.ANSWERED&&
    x.averageHundredths===(x.statuses.ANSWERED?Math.floor(ratings.reduce((sum,k)=>sum+Number(k)*x.ratings[k],0)*100/x.statuses.ANSWERED):null);
}
export function SurveyReport({org,loc,from,to,enabled,onDenied}:{org:string;loc:string;from:string;to:string;enabled:boolean;onDenied:()=>void}){
  const [report,setReport]=React.useState<Report|null>(null),[message,setMessage]=React.useState('');
  const [busy,setBusy]=React.useState(false);const ctrl=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>ctrl.current?.abort(),[]);
  async function load(){
    ctrl.current?.abort();const c=new AbortController();ctrl.current=c;setReport(null);setBusy(true);setMessage('');
    try{
      const res=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/locations/'+
        encodeURIComponent(loc)+'/crm/survey-report?'+new URLSearchParams({fromDay:from,toDay:to}),
        {credentials:'include',cache:'no-store',signal:c.signal});
      if(c.signal.aborted)return;
      if([401,403,404].includes(res.status)){setReport(null);onDenied();return;}
      if(res.status===409||res.status===422){setMessage('Сократите период или проверьте даты.');return;}
      if(!res.ok)throw Error('report');
      const data=await res.json();
      if(!validReport(data,from,to))throw Error('shape');
      if(!c.signal.aborted)setReport(data);
    }catch{if(!c.signal.aborted)setMessage('Не удалось подтвердить сводку.');}
    finally{if(!c.signal.aborted)setBusy(false);}
  }
  return <section aria-label="Сводка опросов"><h2>Оценки обслуживания</h2>
    <p>Период создания опросов, местное время филиала. Только синтетические ответы.</p>
    <button type="button" disabled={!enabled||busy} onClick={()=>void load()}>Построить сводку опросов</button>
    {message&&<p role="alert">{message}</p>}
    {report&&<div><p>Всего опросов: {report.total}. Средняя оценка: {report.averageHundredths===null?'нет ответов':(report.averageHundredths/100).toLocaleString('ru-RU')}.</p>
      <ul>{(Object.keys(statuses) as Status[]).map(k=><li key={k}>{statuses[k]}: {report.statuses[k]}</li>)}</ul>
      <h3>Распределение оценок</h3><ul>{[1,2,3,4,5].map(n=><li key={n}>{n}: {report.ratings[String(n)]}</li>)}</ul>
      <h3>Причины</h3><ul>{reasons.map(k=><li key={k}>{labels[k]}: {report.reasons[k]}</li>)}</ul></div>}
  </section>;
}
