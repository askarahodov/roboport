import React from 'react';
import { Link } from 'react-router-dom';

type AcceptedInvitation = {
  status:'ACCEPTED';
  organizationId:string;
  grantId:string;
  role:string;
  locationId:string|null;
};

export function AutoStaffInvitationAccept() {
  const [token,setToken]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [accepted,setAccepted]=React.useState<AcceptedInvitation|null>(null);

  async function submit(event:React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(busy||!token.trim())return;
    setBusy(true);setMessage('');setAccepted(null);
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if(!csrf.ok)throw new Error('AUTH');
      const csrfBody:unknown=await csrf.json();
      if(!csrfBody||typeof csrfBody!=='object'||!('csrfToken' in csrfBody)||
        typeof csrfBody.csrfToken!=='string')throw new Error('CSRF');
      const response=await fetch('/api/v1/dev/auto/staff-invitations/accept',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrfBody.csrfToken},
        body:JSON.stringify({token:token.trim()}),
      });
      if(response.status===410){setMessage('Срок действия приглашения истёк. Попросите администратора создать новое.');return;}
      if(response.status===409){setMessage('Приглашение уже принято, отозвано или назначение уже существует.');return;}
      if([401,403,404].includes(response.status)){setMessage('Доступ запрещён. Войдите под той учётной записью, которой адресовано приглашение.');return;}
      if(response.status===422){setMessage('Код приглашения имеет неверный формат.');return;}
      if(!response.ok)throw new Error('HTTP');
      const value:unknown=await response.json();
      if(!value||typeof value!=='object'||!('status' in value)||value.status!=='ACCEPTED'||
        !('organizationId' in value)||typeof value.organizationId!=='string'||
        !('grantId' in value)||typeof value.grantId!=='string'||
        !('role' in value)||typeof value.role!=='string'||
        !('locationId' in value)||(value.locationId!==null&&typeof value.locationId!=='string'))
        throw new Error('INVALID_RESPONSE');
      setAccepted(value as AcceptedInvitation);
      setToken('');
      setMessage('Приглашение принято. Доступ к организации выдан сервером.');
    }catch(error){
      setMessage(error instanceof Error&&error.message==='AUTH'
        ?'Сессия недоступна. Войдите в систему и повторите попытку.'
        :'Не удалось принять приглашение. Проверьте соединение и повторите запрос.');
    }finally{setBusy(false);}
  }

  return <section aria-label="Принятие приглашения сотрудника" style={{maxWidth:'44rem',margin:'2rem auto',padding:'0 1rem'}}>
    <h1>Приглашение в организацию</h1>
    <p>Введите одноразовый код, полученный от администратора. Код привязан к конкретной активной учётной записи; перед принятием войдите именно под ней.</p>
    <p><Link to="/account/login">Войти в учётную запись</Link></p>
    <form onSubmit={submit}>
      <label>Одноразовый код приглашения
        <input aria-label="Одноразовый код приглашения" value={token}
          onChange={e=>setToken(e.target.value)} autoComplete="off"
          spellCheck={false} maxLength={64} required />
      </label>
      <button type="submit" disabled={busy||token.trim().length<40}>
        {busy?'Проверяем…':'Принять приглашение'}
      </button>
    </form>
    {message&&<p role="status">{message}</p>}
    {accepted&&<section aria-label="Назначение принято">
      <h2>Доступ активирован</h2>
      <p>Роль: <strong>{accepted.role}</strong></p>
      <p>Организация: {accepted.organizationId}</p>
      <p>Филиал: {accepted.locationId??'Все филиалы'}</p>
      <p>Идентификатор назначения: {accepted.grantId}</p>
    </section>}
    <p>Если код потерян или отозван, попросите администратора создать новое приглашение. Код не хранится в браузере и не может быть восстановлен из списка.</p>
  </section>;
}
