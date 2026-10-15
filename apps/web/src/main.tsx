import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Theme = 'auto'|'light'|'dark';
function App(){
 const [theme,setTheme]=useState<Theme>('auto');
 useEffect(()=>{document.documentElement.dataset.theme=theme},[theme]);
 const areas=['Каталог моделей','Личный кабинет клиента','CRM сотрудников'];
 return <main className="shell">
  <header className="bar"><div className="identity"><span className="mark" aria-hidden="true">R</span><strong>ROBO<span>port</span></strong><small>сервис робототехники</small></div>
    <label>Тема <select aria-label="Цветовая тема" value={theme} onChange={e=>setTheme(e.target.value as Theme)}><option value="auto">Авто</option><option value="light">Дневная</option><option value="dark">Ночная</option></select></label></header>
  <section className="hero"><p className="eyebrow">ДЕМОНСТРАЦИЯ · разработка</p><h1>ROBOport</h1><p>Единая платформа сервиса бытовой робототехники.</p><p>Каталог, личный кабинет и CRM находятся на стадии разработки. Настоящие заявки и вход отключены.</p></section>
  <section aria-label="Разделы платформы" className="cards">{areas.map(a=><article key={a}><h2>{a}</h2><p>Раздел пока не подключён. Для согласованных макетов откройте design/prototype/index.html.</p><button type="button" disabled>Скоро появится</button></article>)}</section>
  <footer>ROBOport · рабочее название · только синтетические данные</footer>
 </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
