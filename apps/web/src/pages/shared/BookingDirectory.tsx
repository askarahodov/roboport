import React from 'react';

export type DirectoryItem={id:string;label:string;durationMinutes:number|null;available:number|null};
type Kind='bays'|'vehicles'|'employees'|'services'|'stock'|'complexes';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const names:Record<Kind,string>={bays:'Посты',vehicles:'Автомобили',employees:'Специалисты',
  services:'Работы',stock:'Складские позиции',complexes:'Комплексы работ'};

export function BookingDirectory({org,loc,kind,disabled,onChoose,onDenied,customerLink}:{
  org:string;loc:string;kind:Kind;disabled:boolean;customerLink?:string;
  onChoose:(item:DirectoryItem)=>void;onDenied?:()=>void
}){
  const [open,setOpen]=React.useState(false),[search,setSearch]=React.useState('');
  const [submitted,setSubmitted]=React.useState('');
  const [after,setAfter]=React.useState<string|null>(null),[next,setNext]=React.useState<string|null>(null);
  const [items,setItems]=React.useState<DirectoryItem[]>([]);
  const [status,setStatus]=React.useState(''),[refresh,setRefresh]=React.useState(0);
  const denied=React.useRef(onDenied);denied.current=onDenied;
  React.useEffect(()=>{
    setItems([]);setNext(null);setAfter(null);setOpen(false);setSearch('');setSubmitted('');setStatus('');
  },[org,loc,kind,customerLink]);
  React.useEffect(()=>{
    if(!open||!uuid.test(org)||!uuid.test(loc)||(customerLink&&!uuid.test(customerLink)))return;
    const controller=new AbortController();
    setItems([]);setNext(null);setStatus('Поиск…');
    const query=new URLSearchParams(customerLink?{organizationId:org,locationId:loc,
      customerLinkId:customerLink,search:submitted}:{kind,search:submitted});
    if(after)query.set('afterId',after);
    const path=customerLink?'/api/v1/dev/auto/customer/booking/service-directory?'+query:
      '/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/booking-directory?'+query;
    void fetch(path,{credentials:'include',cache:'no-store',signal:controller.signal}).then(async res=>{
      if(controller.signal.aborted)return;
      if([401,403,404].includes(res.status)){
        setStatus('Нет доступа к справочнику.');denied.current?.();return;
      }
      if(!res.ok)throw Error('directory');
      const raw=await res.json() as {syntheticOnly?:unknown;items?:unknown;nextAfterId?:unknown};
      if(raw.syntheticOnly!==true||!Array.isArray(raw.items)||raw.items.length>50||
        !raw.items.every((x:DirectoryItem)=>x&&uuid.test(x.id)&&typeof x.label==='string'&&
          (x.durationMinutes===null||(Number.isSafeInteger(x.durationMinutes)&&x.durationMinutes>=1))&&
          (x.available===null||(Number.isSafeInteger(x.available)&&x.available>=0)))||
        !(raw.nextAfterId===null||(typeof raw.nextAfterId==='string'&&uuid.test(raw.nextAfterId)&&
          raw.nextAfterId!==after&&raw.items.length>0)))throw Error('shape');
      if(controller.signal.aborted)return;
      setItems(raw.items as DirectoryItem[]);setNext(raw.nextAfterId as string|null);
      setStatus(raw.items.length?'':'Совпадений нет.');
    }).catch(()=>{if(!controller.signal.aborted)setStatus('Не удалось загрузить справочник.');});
    return ()=>controller.abort();
  },[org,loc,kind,customerLink,open,submitted,after,refresh]);
  return <div aria-label={'Справочник: '+names[kind]}>
    <button type="button" disabled={disabled} onClick={()=>setOpen(x=>!x)}>
      {open?'Закрыть: ':'Найти: '}{names[kind]}</button>
    {open&&<>
      <label>Поиск: {names[kind]} <input maxLength={64} value={search}
        disabled={disabled} onChange={e=>setSearch(e.target.value)}/></label>
      <button type="button" disabled={disabled} onClick={()=>{
        setAfter(null);setSubmitted(search.trim());setRefresh(n=>n+1);
      }}>Найти в справочнике</button>
      {status&&<p role="status">{status}</p>}
      <ul>{items.map(x=><li key={x.id}><button type="button" disabled={disabled}
        onClick={()=>{onChoose(x);setOpen(false);setAfter(null);}}>
        {x.label}{x.durationMinutes!==null?' · '+x.durationMinutes+' мин.':''}
        {x.available!==null?' · доступно '+x.available:''}
      </button></li>)}</ul>
      {after&&<button type="button" disabled={disabled} onClick={()=>setAfter(null)}>Первая страница</button>}
      {next&&<button type="button" disabled={disabled} onClick={()=>setAfter(next)}>Следующие позиции</button>}
    </>}
  </div>;
}
