import React from 'react';

export function CustomerSettings() {
  const [enabled,setEnabled]=React.useState(true);
  const [status,setStatus]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [saving,setSaving]=React.useState(false);
  const [message,setMessage]=React.useState('');
  const [retry,setRetry]=React.useState(0);
  React.useEffect(() => {
    const controller=new AbortController();
    setStatus('loading');
    async function fetchSetting() {
      try {
        const res=await fetch('/api/v1/dev/account/preferences',{
          credentials:'include',cache:'no-store',signal:controller.signal,
        });
        if ([401,403,404].includes(res.status)) {setStatus('denied');return;}
        if (!res.ok) throw new Error('Settings unavailable');
        const body:unknown=await res.json();
        if (!body || typeof body!=='object' || !('inAppEnabled' in body) ||
            typeof body.inAppEnabled!=='boolean') throw new Error('Bad setting');
        if (!controller.signal.aborted) {
          setEnabled(body.inAppEnabled);setStatus('ready');
        }
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    }
    void fetchSetting();
    return ()=>controller.abort();
  },[retry]);
  async function save() {
    if (saving) return;
    setSaving(true);
    setMessage('');
    try {
      const csrfRes=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store',
      });
      if (!csrfRes.ok) throw new Error('CSRF');
      const token:unknown=await csrfRes.json();
      if (!token || typeof token!=='object' || !('csrfToken' in token) ||
          typeof token.csrfToken!=='string') throw new Error('CSRF');
      const res=await fetch('/api/v1/dev/account/preferences',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body:JSON.stringify({inAppEnabled:enabled}),
      });
      if ([401,403,404].includes(res.status)) {
        setStatus('denied');throw new Error('Access revoked');
      }
      if (!res.ok) throw new Error('Saving failed');
      setMessage('Настройки сохранены.');
    } catch {
      setMessage('Не удалось сохранить настройки. Повторите попытку.');
    } finally {setSaving(false);}
  }
  if (status==='loading') return <p role="status">Загрузка настроек…</p>;
  if (status==='denied') return <p role="alert">Доступ к настройкам отозван или сессия истекла.</p>;
  if (status==='error') return <div><p role="alert">Настройки недоступны.</p>
    <button type="button" onClick={()=>setRetry(n=>n+1)}>Повторить запрос</button></div>;
  return <div>
    <label><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>
      Внутренние уведомления в личном кабинете</label>
    <p>SMS и email на синтетическом стенде не отправляются.</p>
    <button type="button" disabled={saving} onClick={()=>void save()}>
      {saving?'Сохранение…':'Сохранить настройки'}</button>
    {message && <p role="status">{message}</p>}
  </div>;
}
