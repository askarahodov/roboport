import React from 'react';

type Estimate = { id:string; version?:number; status?:string };

export function EstimateActions({ estimate, afterSuccess }: {
  estimate:Estimate; afterSuccess:()=>void;
}) {
  const [reason,setReason]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  if (estimate.status!=='SENT' || typeof estimate.version!=='number') return null;
  async function decide(decision:'approve'|'decline') {
    if (busy) return;
    if (decision==='decline' && reason.trim().length<3) {
      setMessage('Укажите причину отказа (минимум 3 символа).');
      return;
    }
    setBusy(true);setMessage('');
    try {
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if (!csrf.ok) throw new Error('SESSION');
      const data:unknown=await csrf.json();
      if (!data || typeof data!=='object' || !('csrfToken' in data) ||
        typeof data.csrfToken!=='string') throw new Error('SESSION');
      const response=await fetch('/api/v1/customer/estimates/'+encodeURIComponent(estimate.id)+'/'+decision,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrfToken,
          'Idempotency-Key':crypto.randomUUID()},
        body:JSON.stringify(decision==='approve'
          ? {estimateVersion:estimate.version,approvalMethod:'verified_account'}
          : {estimateVersion:estimate.version,reason:reason.trim()}),
      });
      if (response.status===409) {
        setMessage('409 — смета изменилась. Загрузите актуальную версию перед решением.');
        return;
      }
      if ([401,403,404].includes(response.status)) {
        setMessage('Доступ отозван или сессия истекла. Решение не принято.');
        return;
      }
      if (!response.ok) throw new Error('FAILED');
      setMessage('Решение сохранено на сервере.');
      afterSuccess();
    } catch {
      setMessage('Не удалось подтвердить решение на сервере.');
    } finally {setBusy(false);}
  }
  return <div className="estimate-actions">
    <p>Решение будет принято только для версии {estimate.version} и после проверки вашей сессии.</p>
    <button type="button" disabled={busy} onClick={()=>void decide('approve')}>Согласовать смету</button>
    <label>Причина отказа <textarea value={reason} onChange={e=>setReason(e.target.value)}
      rows={2} maxLength={1000}/></label>
    <button type="button" disabled={busy} onClick={()=>void decide('decline')}>Отказать</button>
    {message && <p role="status">{message}</p>}
  </div>;
}
