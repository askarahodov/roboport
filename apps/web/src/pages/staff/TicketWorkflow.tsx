import React from 'react';

type Ticket={id:string;state:string;version:number};
const targets:Record<string,string[]>={
  DRAFT:['SUBMITTED'],SUBMITTED:['TRIAGE'],TRIAGE:['AWAITING_DELIVERY'],
  AWAITING_DELIVERY:['RECEIVED'],RECEIVED:['DIAGNOSING'],
  DIAGNOSING:['SAFETY_HOLD','ESTIMATE_DRAFT'],
  ESTIMATE_DRAFT:['AWAITING_APPROVAL'],AWAITING_APPROVAL:['ESTIMATE_DRAFT','APPROVED','DECLINED'],
  APPROVED:['AWAITING_PARTS','REPAIRING'],AWAITING_PARTS:['REPAIRING'],
  REPAIRING:['QC_PENDING'],QC_PENDING:['QC_FAILED','READY_FOR_HANDOVER'],
  QC_FAILED:['REPAIRING'],READY_FOR_HANDOVER:['COMPLETED'],
};
const intake=new Set(['DRAFT','SUBMITTED','TRIAGE','AWAITING_DELIVERY']);
const engineering=new Set(['RECEIVED','DIAGNOSING','REPAIRING','QC_FAILED']);
const labels:Record<string,string>={
  SUBMITTED:'Передать на регистрацию',TRIAGE:'Начать сортировку',
  AWAITING_DELIVERY:'Ожидать доставки',RECEIVED:'Принять устройство',
  DIAGNOSING:'Начать диагностику',SAFETY_HOLD:'Заблокировать по безопасности',
  ESTIMATE_DRAFT:'Подготовить смету',AWAITING_APPROVAL:'Передать смету на согласование',
  APPROVED:'Согласовано',DECLINED:'Отклонено',AWAITING_PARTS:'Ожидать детали',
  REPAIRING:'Вернуть в ремонт',QC_PENDING:'Передать на QC',
  QC_FAILED:'QC не пройден',READY_FOR_HANDOVER:'QC пройден, готово к выдаче',
  COMPLETED:'Выдать устройство',
};
export function TicketWorkflow({ticket,roles,onChanged}:{
  ticket:Ticket;roles:string[];onChanged:()=>void;
}){
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [review,setReview]=React.useState<'approve'|'reject'>('approve');
  // Server FSM requires ticket.manage for QC_PENDING and READY_FOR_HANDOVER.
  const allowed=(intake.has(ticket.state)&&roles.includes('INTAKE'))||
    (engineering.has(ticket.state)&&roles.includes('ENGINEER'))||
    (!intake.has(ticket.state)&&!engineering.has(ticket.state)&&roles.includes('MANAGER'));
  const choices=(targets[ticket.state]||[]).filter(target=>
    !(target==='COMPLETED'&&ticket.state!=='READY_FOR_HANDOVER'));
  async function send(path:string,payload:object){
    if(busy)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw new Error('401 — повторите вход');
      const body:unknown=await csrf.json();
      if(!body||typeof body!=='object'||!('csrfToken' in body)||typeof body.csrfToken!=='string')
        throw new Error('Сервер не выдал CSRF');
      const response=await fetch(path,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':body.csrfToken,
          'Idempotency-Key':crypto.randomUUID()},
        body:JSON.stringify(payload),
      });
      if(response.status===409){setMessage('409 — версия заявки изменилась, обновите очередь.');return;}
      if([401,403,404].includes(response.status)){setMessage(response.status+' — доступ запрещён или сессия отозвана.');return;}
      if(!response.ok){setMessage('Ошибка сервера '+response.status);return;}
      setMessage('Событие сохранено сервером.');
      onChanged();
    }catch(e){setMessage(e instanceof Error?e.message:'Не удалось отправить запрос');}
    finally{setBusy(false);}
  }
  const url='/api/v1/dev/tickets/'+encodeURIComponent(ticket.id);
  return <div aria-label={'Операции заявки '+ticket.id}>
    {ticket.state==='SAFETY_HOLD'?<>
      <p role="alert">Safety hold: любые ремонтные операции заблокированы до независимого review.</p>
      {(roles.includes('ENGINEER')||roles.includes('MANAGER'))&&<>
        <label>Решение эксперта <select value={review} onChange={e=>setReview(e.target.value as 'approve'|'reject')}>
          <option value="approve">Одобрить</option><option value="reject">Отклонить</option></select></label>
        <button disabled={busy} onClick={()=>void send(url+'/safety-review',{
          expectedVersion:ticket.version,approved:review==='approve'})}>Зафиксировать review</button>
      </>}
      {roles.includes('MANAGER')&&<button disabled={busy} onClick={()=>void send(url+'/safety-release',{
        expectedVersion:ticket.version})}>Снять блокировку после двух независимых решений</button>}
    </>:allowed&&choices.map(target=><button type="button" key={target} disabled={busy}
      onClick={()=>void send(url+'/transition',{toState:target,expectedVersion:ticket.version})}>
      {labels[target]||target}</button>)}
    {message&&<p role="status">{message}</p>}
  </div>;
}
