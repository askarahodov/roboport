import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

export function CustomerLogin() {
  const navigate = useNavigate();
  const [login,setLogin]=React.useState('');
  const [password,setPassword]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState<string|null>(null);
  async function submit(event:React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // Browser sends HttpOnly cookie; no bearer token, no localStorage.
      const response=await fetch('/api/v1/auth/login',{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({login,password}),
      });
      if (response.status===401) { setError('Неверный логин или пароль.'); return; }
      if (response.status===429) { setError('Слишком много попыток. Повторите позже.'); return; }
      if (response.status===403) { setError('Вход доступен только на защищённом локальном HTTPS-стенде.'); return; }
      if (!response.ok) { setError('Не удалось войти. Проверьте подключение.'); return; }
      // csrfToken is intentionally not persisted. All mutations must obtain a fresh token
      // from the trusted server session; this page performs only login.
      setPassword('');
      navigate('/account',{replace:true});
    } catch { setError('Сервер авторизации недоступен.'); }
    finally { setBusy(false); }
  }
  return <section><h1>Вход в личный кабинет</h1>
    <p>Только для синтетических локальных учётных записей. Саморегистрация отключена.</p>
    <form onSubmit={event=>void submit(event)}>
      <label>Логин <input autoComplete="username" required minLength={3} maxLength={128}
        value={login} onChange={event=>setLogin(event.target.value)} /></label>
      <label>Пароль <input autoComplete="current-password" required type="password"
        value={password} onChange={event=>setPassword(event.target.value)} /></label>
      <button type="submit" disabled={busy}>{busy?'Проверка…':'Войти'}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    <p><Link to="/account">Назад</Link></p>
  </section>;
}
