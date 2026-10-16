import React from 'react';
export function StaffAudit(){
  const [items,setItems]=React.useState<{id:string;ticketId:string;action:string;createdAt:string}[]>([]);
  const [state,setState]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [attempt,setAttempt]=React.useState(0);
  React.useEffect(()=>{
    const c=new AbortController();setState('loading');setItems([]);
    void (async()=>{
      try{
        const r=await fetch('/api/v1/dev/staff/audit?limit=30',
          {signal:c.signal,credentials:'include',cache:'no-store'});
        if(c.signal.aborted)return;
        if([401,403,404].includes(r.status)){setState('denied');return;}
        if(!r.ok)throw new Error('audit error');
        const v:unknown=await r.json();
        if(!v||typeof v!=='object'||!('items' in v)||!Array.isArray(v.items)||
          !v.items.every((item:unknown)=>item&&typeof item==='object'&&
            'id' in item&&typeof item.id==='string'&&
            'action' in item&&typeof item.action==='string'&&
            'ticketId' in item&&typeof item.ticketId==='string'&&
            'createdAt' in item&&typeof item.createdAt==='string'))throw new Error('invalid audit');
        if(!c.signal.aborted){setItems(v.items);setState('ready');}
      }catch{if(!c.signal.aborted)setState('error');}
    })();
    return ()=>c.abort();
  },[attempt]);
  return <section aria-label="Журнал аудита"><h2>Аудит операций</h2>
    {state==='loading'&&<p role="status">Загрузка аудита…</p>}
    {state==='denied'&&<p role="alert">Доступ к журналу запрещён.</p>}
    {state==='error'&&<><p role="alert">Ошибка загрузки аудита.</p>
      <button onClick={()=>setAttempt(x=>x+1)}>Повторить аудит</button></>}
    {state==='ready'&&(items.length?
      <ul>{items.map(item=><li key={item.id}>
        {item.createdAt}: {item.action}, заявка {item.ticketId}
      </li>)}</ul>:<p role="status">Доступных событий нет.</p>)}
  </section>;
}
