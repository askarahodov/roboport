import React from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
function App() {
 const [dark,setDark] = React.useState(false);
 return <main className={dark ? 'app dark' : 'app'}>
 <header><picture><source media="(prefers-color-scheme: dark)" srcSet="/logo-dark.svg"/><img src="/logo-light.svg" alt="ROBOport" width="64" height="64"/></picture><strong>ROBOport</strong><button onClick={()=>setDark(!dark)} aria-label="Переключить тему">Тема</button></header>
 <section><h1>Сервис обслуживания роботов</h1><p>Синтетический стенд разработки. Приём реальных заявок пока закрыт.</p></section>
 </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
