import React from 'react';

const channels=['CALL','EMAIL','SMS'] as const;
type Channel=typeof channels[number];
type ChoiceMap=Record<Channel,boolean>;
type Snapshot={syntheticOnly:true;sendAllowed:false;channels:ChoiceMap};
const labels:Record<Channel,string>={CALL:'Звонки',EMAIL:'Электронная почта',SMS:'SMS'};

function parse(raw:unknown):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('choice');
  const x=raw as Partial<Snapshot>;
  if(x.syntheticOnly!==true||x.sendAllowed!==false||
     !x.channels||channels.some(code=>typeof x.channels?.[code]!=='boolean'))
    throw Error('choice');
  return x as Snapshot;
}

export function StaffCrmContactPreferences({
  base,leadId,onDenied,
}:{base:string;leadId:string;onDenied:()=>void}){
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'error'|'denied'>('idle');
  const [data,setData]=React.useState<Snapshot|null>(null);
  React.useEffect(()=>{
    setStatus('idle');setData(null);
  },[leadId,base]);
  const controller=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>controller.current?.abort(),[]);

  async function load(){
    controller.current?.abort();
    const pending=new AbortController();
    controller.current=pending;
    setStatus('loading');setData(null);
    try{
      const url=base+'/'+encodeURIComponent(leadId)+'/contact-preferences';
      const response=await fetch(url,{
        credentials:'include',cache:'no-store',signal:pending.signal,
      });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setData(null);setStatus('denied');onDenied();return;
      }
      if(!response.ok)throw Error('http');
      const snap=parse(await response.json());
      if(!pending.signal.aborted){setData(snap);setStatus('ready');}
    }catch{
      if(!pending.signal.aborted){setData(null);setStatus('error');}
    }
  }

  return <section aria-label="Предпочтения связи клиента в CRM">
    <h3>Предпочтения способов связи</h3>
    <p>Просмотр только для сотрудников с доступом к обращению.
      Это не согласие на маркетинг и не право на реальную отправку.</p>
    <button type="button" disabled={status==='loading'}
      onClick={()=>void load()}>
      Проверить предпочтения клиента</button>
    {status==='loading'&&<p role="status">Проверка действующих прав и выбора…</p>}
    {status==='error'&&<p role="status">Не удалось проверить предпочтения.
      Повторите запрос.</p>}
    {status==='ready'&&data&&<div>
      <p>Внешние сообщения: отправка запрещена.</p>
      <ul>{channels.map(code=><li key={code}>{labels[code]}: {
        data.channels[code]?'Клиент отметил предпочтение':'Клиент не разрешал'
      }</li>)}</ul>
    </div>}
  </section>;
}
