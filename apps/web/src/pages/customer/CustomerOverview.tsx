import React from 'react';
import { Link } from 'react-router-dom';

type Section = 'robots'|'tickets'|'estimates'|'documents';
type State = {status:'loading'|'ready'|'denied'|'error';count:number};
const sections:{key:Section;label:string;url:string}[]=[
 {key:'robots',label:'Роботы',url:'/account/robots'},
 {key:'tickets',label:'Заявки',url:'/account/tickets'},
 {key:'estimates',label:'Сметы',url:'/account/estimates'},
 {key:'documents',label:'Документы',url:'/account/documents'},
];
export function CustomerOverview(){
 const [reload,setReload]=React.useState(0);
 const [states,setStates]=React.useState<Record<Section,State>>({
  robots:{status:'loading',count:0},tickets:{status:'loading',count:0},
  estimates:{status:'loading',count:0},documents:{status:'loading',count:0},
 });
 React.useEffect(()=>{
  const controller=new AbortController();
  setStates(Object.fromEntries(sections.map(s=>[s.key,{status:'loading',count:0}])) as Record<Section,State>);
  for(const section of sections){
   const path=section.key==='tickets'?'/api/v1/dev/account/tickets?limit=20&offset=0':
    '/api/v1/dev/account/summary/'+section.key;
   (async()=>{let state:State;
    try{
     const response=await fetch(path,{credentials:'include',cache:'no-store',signal:controller.signal});
     if([401,403,404].includes(response.status))state={status:'denied',count:0};
     else if(!response.ok)state={status:'error',count:0};
     else{
      const data:unknown=await response.json();
      if(!data||typeof data!=='object'||!('items' in data)||!Array.isArray(data.items)||
       !data.items.every((item:unknown)=>item!==null&&typeof item==='object'&&
        'id' in item&&typeof item.id==='string'))throw Error('Invalid payload');
      state={status:'ready',count:data.items.length};
     }
    }catch{state={status:'error',count:0};}
    if(!controller.signal.aborted)setStates(old=>({...old,[section.key]:state}));
   })();
  }
  return()=>controller.abort();
 },[reload]);
 return <section aria-label="Обзор клиентского кабинета">
  <h3>Ваши данные</h3>
  <p>Демонстрационный стенд. Сведения доступны только после серверной проверки прав.</p>
  <ul className="account-stats">{sections.map(s=><li className="account-stat" key={s.key}><Link className="account-stat-link" to={s.url}>{s.label} <span aria-hidden="true">↗</span></Link>
   {states[s.key].status==='loading'?<span role="status">загрузка…</span>:
    states[s.key].status==='denied'?<span role="alert">доступ отозван</span>:
    states[s.key].status==='error'?<span role="alert">ошибка загрузки</span>:
    <span>{states[s.key].count===0?'нет доступных записей':
      'записей на странице: '+states[s.key].count}</span>}</li>)}</ul>
  <button type="button" onClick={()=>setReload(x=>x+1)}>Обновить обзор</button>
 </section>;
}
