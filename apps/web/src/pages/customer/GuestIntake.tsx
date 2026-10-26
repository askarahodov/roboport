import React from 'react';
import { Link } from 'react-router-dom';
import { PageShell, Card, Alert, FormField, Button, Badge } from '../../ui';

/** Synthetic-only anonymous intake: never collect names, contacts or real device data. */
export function GuestIntake(){
 const [status,setStatus]=React.useState<'idle'|'sending'|'pending'|'error'>('idle');
 const [receipt,setReceipt]=React.useState('');
 const key=React.useRef<string>('');
 async function submit(){
  if(status==='sending')return;
  key.current ||= crypto.randomUUID()+crypto.randomUUID();
  setStatus('sending');
  try{
   const r=await fetch('/api/v1/dev/guest-intake',{method:'POST',credentials:'omit',cache:'no-store',
     headers:{'Content-Type':'application/json','Idempotency-Key':key.current},body:'{}'});
   if(!r.ok)throw Error('intake denied');
   const data:unknown=await r.json();
   if(!data||typeof data!=='object'||!('status' in data)||data.status!=='PENDING_REVIEW'
      ||!('requestId' in data)||typeof data.requestId!=='string')throw Error('invalid receipt');
   setReceipt(data.requestId);setStatus('pending');
  }catch{setStatus('error');}
 }
 return <PageShell><Card><Badge tone="warning">Локальный демонстрационный сценарий</Badge>
   <h1>Тестовая заявка на обслуживание</h1>
   <p>Для проверки процесса создаётся синтетическая заявка без персональных данных и без реального робота. Приём реальных обращений закрыт.</p>
   {status==='pending'?<div role="status"><h2>Заявка ожидает проверки</h2>
       <p>Статус: <strong>pending_review</strong>. Доступ к истории, сметам и документам не предоставлен.</p>
       <p>Номер запроса: <code>{receipt}</code></p>
       <p>Сотрудник должен подтвердить заявку отдельно. Сохраните номер только для демонстрационного стенда.</p>
       <Link to="/account/claim">У меня есть доказательство для подтверждения</Link>
     </div>:<>
       <Button onClick={()=>void submit()} disabled={status==='sending'}>{status==='sending'?'Создание…':'Создать тестовую заявку'}</Button>
       {status==='error'&&<Alert>Не удалось создать заявку. Проверьте локальный HTTPS-стенд и повторите запрос.</Alert>}
     </>}
 </Card></PageShell>;
}
export function ClaimProof(){
 const [claimId,setClaimId]=React.useState('');
 const [proof,setProof]=React.useState('');
 const [status,setStatus]=React.useState<'idle'|'submitting'|'pending'|'error'>('idle');
 async function submit(e:React.FormEvent){
  e.preventDefault();setStatus('submitting');
  try{
   const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
   if(!csrf.ok)throw Error('no session');
   const token=await csrf.json() as {csrfToken?:string};
   if(!token.csrfToken)throw Error('invalid csrf');
   const r=await fetch('/api/v1/dev/guest-claims/'+encodeURIComponent(claimId)+'/redeem',
     {method:'POST',credentials:'include',cache:'no-store',
      headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
      body:JSON.stringify({proof})});
   if(!r.ok)throw Error('denied');
   const data:unknown=await r.json();
   if(!data||typeof data!=='object'||!('status' in data)||data.status!=='PENDING_REVIEW')throw Error('not verified');
   setProof('');setStatus('pending');
  }catch{setStatus('error');setProof('');}
 }
 return <PageShell><Card><h1>Подтверждение тестовой заявки</h1>
   <p>Войдите в личный кабинет. Код подтверждения выдаёт уполномоченный сотрудник через отдельный канал; одного номера заявки недостаточно.</p>
   <p><Link to="/account/login">Войти в кабинет</Link></p>
   {status==='pending'?<Alert tone="info">Доказательство принято. Статус pending_review: доступ появится только после независимого решения сотрудника.</Alert>:
    <form onSubmit={e=>void submit(e)}>
     <FormField id="claim-id" label="Идентификатор подтверждения"><input id="claim-id" autoComplete="off" required value={claimId} onChange={e=>setClaimId(e.target.value)} /></FormField>
     <FormField id="claim-proof" label="Код подтверждения"><input id="claim-proof" type="password" autoComplete="off" required value={proof} onChange={e=>setProof(e.target.value)} /></FormField>
     <Button type="submit" disabled={status==='submitting'}>Подтвердить</Button>
     {status==='error'&&<Alert>Не удалось подтвердить код. Проверьте вход и актуальность подтверждения.</Alert>}
    </form>}
 </Card></PageShell>;
}
