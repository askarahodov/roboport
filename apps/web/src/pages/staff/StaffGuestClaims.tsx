import React from 'react';
import { Alert, Button, Card, FormField, EmptyState } from '../../ui';
type Pending={id:string;createdAt:string};

async function post(url:string,body?:object){
 const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
 if(!csrf.ok)throw Error('session');
 const tokens=await csrf.json() as {csrfToken?:string};
 if(!tokens.csrfToken)throw Error('csrf');
 const result=await fetch(url,{method:'POST',credentials:'include',cache:'no-store',
  headers:{'X-CSRF-Token':tokens.csrfToken,'Content-Type':'application/json'},
  body:JSON.stringify(body??{})});
 if(!result.ok)throw Error('action');
 return await result.json() as Record<string,string>;
}
export function StaffGuestClaims({roles}:{roles:string[]}){
 const [queue,setQueue]=React.useState<Pending[]>([]);
 const [phase,setPhase]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
 const [refresh,setRefresh]=React.useState(0);
 const [proofInfo,setProofInfo]=React.useState<{claimId:string;proof:string}|null>(null);
 const [claim,setClaim]=React.useState('');
 const [reviewQueue,setReviewQueue]=React.useState<string[]>([]);
 const [outcome,setOutcome]=React.useState('');
 const [busy,setBusy]=React.useState(false);
 const [error,setError]=React.useState(false);
 React.useEffect(()=>{
  if(!roles.includes('INTAKE'))return;
  const ctrl=new AbortController();
  setPhase('loading');setQueue([]);
  void (async()=>{try{
    const r=await fetch('/api/v1/dev/guest-intake/queue',{credentials:'include',cache:'no-store',signal:ctrl.signal});
    if([401,403,404].includes(r.status)){setPhase('denied');return;}
    if(!r.ok)throw Error('queue');
    const body=await r.json() as {items?:Pending[]};
    if(!Array.isArray(body.items))throw Error('invalid');
    if(!ctrl.signal.aborted){setQueue(body.items);setPhase('ready');}
  }catch{if(!ctrl.signal.aborted)setPhase('error');}})();
  return()=>ctrl.abort();
 },[roles.includes('INTAKE'),refresh]);
 async function issue(id:string){
   setBusy(true);setError(false);setProofInfo(null);
   try{
    const assigned=await post('/api/v1/dev/guest-intake/'+encodeURIComponent(id)+'/assign');
    const issued=await post('/api/v1/dev/guest-claims/tickets/'+encodeURIComponent(assigned.ticketId)+'/issue');
    if(typeof issued.claimId!=='string'||typeof issued.proof!=='string')throw Error('invalid proof');
    setProofInfo({claimId:issued.claimId,proof:issued.proof});
    setRefresh(n=>n+1);
   }catch{setError(true);}finally{setBusy(false);}
 }
 React.useEffect(()=>{
  if(!roles.includes('MANAGER'))return;
  const ctrl=new AbortController();
  void (async()=>{try{
    const r=await fetch('/api/v1/dev/guest-claims/review-queue',
      {credentials:'include',cache:'no-store',signal:ctrl.signal});
    if(!r.ok)return;
    const data=await r.json() as {items?:{id:string}[]};
    if(!ctrl.signal.aborted&&Array.isArray(data.items)){
      setReviewQueue(data.items.map(x=>x.id));
    }
  }catch{ /* no claims shown if unavailable */ }})();
  return()=>ctrl.abort();
 },[roles.includes('MANAGER'),refresh]);
 async function selectForReview(id:string){
  setBusy(true);setError(false);
  try{
   await post('/api/v1/dev/guest-claims/'+encodeURIComponent(id)+'/assign-review');
   setClaim(id);
  }catch{setError(true);}finally{setBusy(false);}
 }
 async function review(approve:boolean){
  setBusy(true);setError(false);setOutcome('');
  try{
    const result=await post('/api/v1/dev/guest-claims/'+encodeURIComponent(claim)+'/review',{approve});
    setOutcome(result.status);setClaim('');
  }catch{setError(true);}finally{setBusy(false);}
 }
 return <Card><h2>Подтверждение тестовых заявок</h2>
   <p>Сотрудники подтверждают владение отдельно от создания заявки. Прямой доступ клиенту не выдаётся.</p>
   {roles.includes('INTAKE')&&<section aria-label="Очередь гостевых заявок">
     <h3>Ожидают приёмки</h3>
     {phase==='loading'&&<p role="status">Загрузка…</p>}
     {phase==='denied'&&<Alert>Очередь недоступна.</Alert>}
     {phase==='error'&&<Button onClick={()=>setRefresh(n=>n+1)}>Повторить загрузку</Button>}
     {phase==='ready'&&(queue.length?<ul>{queue.map(item=><li key={item.id}>
       <span>Демо-заявка {item.id.slice(0,8)}</span>{' '}
       <Button disabled={busy} onClick={()=>void issue(item.id)}>Назначить и выдать код</Button>
     </li>)}</ul>:<EmptyState title="Ожидающих заявок нет." />)}
     {proofInfo&&<div role="status" className="account-record">
       <strong>Код показан только сейчас. Передайте его клиенту по защищённому отдельному каналу.</strong>
       <p>Claim ID: <code>{proofInfo.claimId}</code></p><p>Код: <code>{proofInfo.proof}</code></p>
       <Button variant="secondary" onClick={()=>setProofInfo(null)}>Скрыть код</Button>
     </div>}
   </section>}
   {roles.includes('MANAGER')&&<form onSubmit={e=>{e.preventDefault();void review(true);}}>
     <h3>Независимое решение менеджера</h3>
     {reviewQueue.length>0&&<ul>{reviewQueue.map(id=><li key={id}><span>Подтверждение {id.slice(0,8)}</span>{' '}<Button type="button" variant="secondary" disabled={busy} onClick={()=>void selectForReview(id)}>Взять на проверку</Button></li>)}</ul>}
     <FormField id="review-claim" label="Идентификатор подтверждения"><input id="review-claim" required autoComplete="off" value={claim} onChange={e=>setClaim(e.target.value)} /></FormField>
     <Button type="submit" disabled={busy}>Подтвердить владение</Button>{' '}
     <Button type="button" variant="secondary" disabled={busy||!claim} onClick={()=>void review(false)}>Отклонить</Button>
     {outcome&&<p role="status">Решение: {outcome}</p>}
   </form>}
   {error&&<Alert>Операция не выполнена: проверьте назначение, права, сессию и состояние заявки.</Alert>}
 </Card>;
}
