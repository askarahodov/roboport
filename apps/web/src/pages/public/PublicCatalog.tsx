import React from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

type Item={id:string;name:string;brandId?:string;category?:string;supportStatus?:string};
type Result={items:Item[];nextCursor?:string|null};
function endpoint(kind:'brands'|'models',cursor:string,limit:number,brand:string){
 const params=new URLSearchParams({limit:String(limit)});
 if(cursor)params.set('cursor',cursor);
 if(kind==='models'&&brand)params.set('brandId',brand);
 return '/api/v1/catalog/'+kind+'?'+params;
}
export function PublicCatalog({kind}:{kind:'brands'|'models'}){
 const [params,setParams]=useSearchParams();
 const cursor=params.get('cursor')||'';
 const brand=params.get('brandId')||'';
 const [items,setItems]=React.useState<Item[]>([]);
 const [next,setNext]=React.useState<string|null>(null);
 const [stack,setStack]=React.useState<string[]>([]);
 const [status,setStatus]=React.useState<'loading'|'ready'|'error'>('loading');
 const [attempt,setAttempt]=React.useState(0);
 React.useEffect(()=>{
  const controller=new AbortController();setItems([]);setNext(null);setStatus('loading');
  (async()=>{try{
    const r=await fetch(endpoint(kind,cursor,20,brand),{signal:controller.signal,credentials:'omit',cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const data:Result=await r.json();
    if(!data||!Array.isArray(data.items)||!data.items.every(x=>typeof x.id==='string'&&typeof x.name==='string'))throw Error('invalid response');
    if(!controller.signal.aborted){setItems(data.items);setNext(typeof data.nextCursor==='string'?data.nextCursor:null);setStatus('ready');}
  }catch{if(!controller.signal.aborted)setStatus('error');}})();
  return()=>controller.abort();
 },[kind,cursor,brand,attempt]);
 function advance(nextCursor:string){setStack(old=>[...old,cursor]);setParams(p=>{const q=new URLSearchParams(p);q.set('cursor',nextCursor);return q;});}
 function back(){const prior=stack[stack.length-1]||'';setStack(old=>old.slice(0,-1));setParams(p=>{const q=new URLSearchParams(p);if(prior)q.set('cursor',prior);else q.delete('cursor');return q;});}
 return <section aria-live="polite">
  <h1>{kind==='brands'?'Бренды роботов':'Модели роботов'}</h1>
  <p>Демонстрационный каталог. Реальные обращения пока не принимаются.</p>
  {kind==='models'&&<form onSubmit={e=>e.preventDefault()}><label>Фильтр по идентификатору бренда <input aria-label="Фильтр бренда" value={brand} onChange={e=>{const val=e.target.value;setStack([]);setParams(p=>{const q=new URLSearchParams(p);q.delete('cursor');if(val)q.set('brandId',val);else q.delete('brandId');return q;});}} /></label></form>}
  {status==='loading'&&<p role="status">Загрузка каталога…</p>}
  {status==='error'&&<><p role="alert">Каталог сейчас недоступен. Попробуйте повторить запрос.</p><button type="button" onClick={()=>setAttempt(x=>x+1)}>Повторить запрос</button></>}
  {status==='ready'&&(items.length?<ul>{items.map(item=><li key={item.id}>{kind==='models'?<Link to={'/catalog/models/'+encodeURIComponent(item.id)}>{item.name}</Link>:item.name}{item.supportStatus&&<span> — {item.supportStatus==='supported'?'Поддержка заявлена в демонстрационных данных':'Поддержка не подтверждена'}</span>}</li>)}</ul>:<p>Пока нет опубликованных {kind==='brands'?'брендов':'моделей'}.</p>)}
  {status==='ready'&&<nav aria-label="Страницы каталога">{stack.length>0&&<button type="button" onClick={back}>Назад</button>}{next&&<button type="button" onClick={()=>advance(next)}>Далее</button>}</nav>}
 </section>;
}
export function PublicModelDetails(){
 const {modelId}=useParams();const [state,setState]=React.useState<'loading'|'found'|'missing'|'error'>('loading');
 const [model,setModel]=React.useState<Item|null>(null);
 const [attempt,setAttempt]=React.useState(0);
 React.useEffect(()=>{const controller=new AbortController();setState('loading');setModel(null);
 (async()=>{try{const r=await fetch('/api/v1/catalog/models/'+encodeURIComponent(modelId||''),{signal:controller.signal,credentials:'omit',cache:'no-store'});
 if(r.status===404){if(!controller.signal.aborted)setState('missing');return;}
 if(!r.ok)throw Error('HTTP '+r.status);
 const data:Item=await r.json();if(!data||typeof data.name!=='string'||typeof data.id!=='string')throw Error('Invalid');
 if(!controller.signal.aborted){setModel(data);setState('found');}
 }catch{if(!controller.signal.aborted)setState('error');}})();return()=>controller.abort();},[modelId,attempt]);
 return <section><Link to="/catalog/models">← Все модели</Link>
 {state==='loading'&&<p role="status">Загрузка модели…</p>}
 {state==='missing'&&<p role="alert">Модель не найдена (404).</p>}
 {state==='error'&&<><p role="alert">Не удалось загрузить модель.</p><button onClick={()=>setAttempt(x=>x+1)}>Повторить</button></>}
 {state==='found'&&model&&<><h1>{model.name}</h1><p>Категория: {model.category||'не указана'}</p><p>Статус поддержки: {model.supportStatus==='supported'?'Заявлена в демонстрационных данных':'Не подтверждён'}</p><p>Приём реальных заявок закрыт.</p></>}
 </section>;
}
