import React from 'react';
import {Link} from 'react-router-dom';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const stages=['ALL','DRAFT','IN_WORK','READY','POSTED',
  'CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL','CANCELED'] as const;
type Stage=typeof stages[number];
const validResultStage=(value:unknown):value is Exclude<Stage,'ALL'>=>
  typeof value==='string' && stages.slice(1).includes(value as Stage);
const names:Record<Stage,string>={
  ALL:'Все состояния',DRAFT:'Черновик',IN_WORK:'В работе',READY:'Работы завершены',
  POSTED:'Технически проведён',CLIENT_ACKNOWLEDGED:'Подтверждён клиентом',
  CLIENT_DISPUTED:'Оспорен клиентом',CLOSED_TECHNICAL:'Технически закрыт',
  CANCELED:'Запись отменена',
};
type Order={workOrderId:string;appointmentId:string;bayId:string;
  createdAt:string;startsAt:string;endsAt:string;stage:Exclude<Stage,'ALL'>};
type Page={syntheticOnly:true;releaseAllowed:false;items:Order[];
  nextCursor:string|null};

function parse(raw:unknown,stage:Stage):Page{
  if(!raw||typeof raw!=='object')throw Error('invalid register');
  const data=raw as Page;
  if(data.syntheticOnly!==true||data.releaseAllowed!==false||
     !Array.isArray(data.items)||data.items.length>25||
     !(data.nextCursor===null||(typeof data.nextCursor==='string'&&
        data.nextCursor.length>0&&data.nextCursor.length<=256)))throw Error('invalid page');
  const ids=new Set<string>();
  for(const row of data.items){
    if(!row||![row.workOrderId,row.appointmentId,row.bayId].every(
         v=>typeof v==='string'&&uuid.test(v))||
      ![row.createdAt,row.startsAt,row.endsAt].every(
         v=>typeof v==='string'&&!Number.isNaN(Date.parse(v)))||
      Date.parse(row.startsAt)>=Date.parse(row.endsAt)||!validResultStage(row.stage)||
      (stage!=='ALL'&&row.stage!==stage)||
      ids.has(row.workOrderId))throw Error('invalid row');
    ids.add(row.workOrderId);
  }
  return data;
}

export function AutoWorkOrderRegister(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [stage,setStage]=React.useState<Stage>('ALL');
  const [search,setSearch]=React.useState('');
  const [applied,setApplied]=React.useState('');
  const [cursor,setCursor]=React.useState<string|null>(null);
  const [history,setHistory]=React.useState<Array<string|null>>([]);
  const [selected,setSelected]=React.useState<string|null>(null);
  const [revision,setRevision]=React.useState(0);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [page,setPage]=React.useState<Page|null>(null);
  const [loadedKey,setLoadedKey]=React.useState('');
  const [exportBusy,setExportBusy]=React.useState(false);
  const [exportMessage,setExportMessage]=React.useState('');
  const exportController=React.useRef<AbortController|null>(null);
  const currentScope=React.useRef('');
  const valid=uuid.test(org.trim())&&uuid.test(loc.trim());
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/orders';
  const scope=org.trim()+':'+loc.trim()+':'+stage+':'+applied;
  const queryKey=scope+':'+cursor+':'+revision;
  currentScope.current=scope;
  React.useEffect(()=>{
    exportController.current?.abort();
    setExportBusy(false);setExportMessage('');
    return ()=>exportController.current?.abort();
  },[scope]);
  React.useEffect(()=>{
    setCursor(null);setHistory([]);setPage(null);setSelected(null);setLoadedKey('');
  },[scope]);
  React.useEffect(()=>{
    setPage(null);setSelected(null);setLoadedKey('');
    if(!valid|| (applied!==''&&!uuid.test(applied))){setStatus('idle');return;}
    const controller=new AbortController();
    setStatus('loading');
    const params=new URLSearchParams({stage});
    if(applied)params.set('orderId',applied);
    if(cursor)params.set('cursor',cursor);
    void fetch(base+'?'+params,{credentials:'include',cache:'no-store',
      signal:controller.signal}).then(async response=>{
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=parse(await response.json(),stage);
      if(!controller.signal.aborted){setPage(data);setLoadedKey(queryKey);setStatus('ready');}
    }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,cursor,revision,valid]);

  function applySearch(event:React.FormEvent){
    event.preventDefault();
    if(search.trim()!==''&&!uuid.test(search.trim()))return;
    setApplied(search.trim().toLowerCase());setCursor(null);
    setHistory([]);setRevision(x=>x+1);
  }
  async function exportRegister(){
    if(!valid||status!=='ready'||loadedKey!==queryKey||exportBusy)return;
    exportController.current?.abort();
    const controller=new AbortController();
    exportController.current=controller;
    const requested=scope;
    setExportBusy(true);setExportMessage('');
    const params=new URLSearchParams({stage});
    if(applied)params.set('orderId',applied);
    try{
      const response=await fetch(base+'/export?'+params,{
        credentials:'include',cache:'no-store',signal:controller.signal,
      });
      if(controller.signal.aborted||currentScope.current!==requested)return;
      if([401,403,404].includes(response.status)){
        setPage(null);setLoadedKey('');setStatus('denied');
        setExportMessage('Сеанс или доступ к филиалу отозван.');return;
      }
      if(response.status===409){
        setExportMessage('Экспорт превышает 1000 строк или данные изменились. '+
          'Уточните фильтр и повторите запрос. Файл не создавался.');return;
      }
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('bad download');
      const blob=await response.blob();
      if(controller.signal.aborted||currentScope.current!==requested)return;
      if(blob.size>1048576)throw Error('unexpected size');
      const href=URL.createObjectURL(blob);
      try{
        const element=document.createElement('a');
        element.href=href;element.download='auto-work-order-register.csv';
        document.body.appendChild(element);element.click();element.remove();
        setExportMessage('CSV реестра сформирован для текущего фильтра (только dev).');
      }finally{window.setTimeout(()=>URL.revokeObjectURL(href),1000);}
    }catch{
      if(!controller.signal.aborted&&currentScope.current===requested)
        setExportMessage('Экспорт не выполнен. Проверьте соединение и обновите реестр.');
    }finally{
      if(exportController.current===controller){
        exportController.current=null;
        if(currentScope.current===requested)setExportBusy(false);
      }
    }
  }
  function detailLink(orderId:string){
    return '/staff/auto-order-services?'+new URLSearchParams({
      org:org.trim(),loc:loc.trim(),order:orderId,
    });
  }
  return <section className="ui-page" aria-label="Реестр заказ-нарядов (dev)"
    style={{maxWidth:'76rem',margin:'1rem auto',overflowWrap:'anywhere'}}>
    <h1>Реестр заказ-нарядов — DEV</h1>
    <p>Поиск, состояние и просмотр только <strong>синтетических</strong> заказов своего
      филиала. Статус «Технически закрыт» не означает фискальную оплату,
      юридическую подпись или физическую выдачу автомобиля.</p>
    <div style={{display:'flex',gap:'0.75rem',flexWrap:'wrap'}}>
      <label>UUID организации <input aria-label="UUID организации" value={org}
        onChange={e=>setOrg(e.target.value)}/></label>
      <label>UUID филиала <input aria-label="UUID филиала" value={loc}
        onChange={e=>setLoc(e.target.value)}/></label>
      <label>Состояние <select aria-label="Состояние заказ-наряда" value={stage}
        onChange={e=>setStage(e.target.value as Stage)}>
        {stages.map(x=><option key={x} value={x}>{names[x]}</option>)}
      </select></label>
    </div>
    <form onSubmit={applySearch}>
      <label>Точный UUID заказ-наряда <input aria-label="Поиск заказ-наряда"
        placeholder="Оставьте пустым для всего филиала"
        value={search} onChange={e=>setSearch(e.target.value)}/></label>
      <button type="submit" disabled={!valid||(search.trim()!==''&&!uuid.test(search.trim()))}>
        Найти в реестре</button>
      <button type="button" onClick={()=>{setSearch('');setApplied('');
        setCursor(null);setHistory([]);setRevision(x=>x+1);}}>Сбросить поиск</button>
    </form>
    {status==='idle'&&<p>Укажите организацию и филиал. Поиск принимает только полный UUID.</p>}
    {status==='loading'&&<p role="status">Загрузка реестра…</p>}
    {status==='denied'&&<p role="alert">403 — нет действующего доступа к заказам этого филиала.</p>}
    {status==='error'&&<p role="alert">Реестр недоступен. Проверьте фильтры, сессию и попробуйте снова.
      <button type="button" onClick={()=>setRevision(x=>x+1)}>Повторить</button></p>}
    {status==='ready'&&page&&loadedKey===queryKey&&<>
      <div>
        <button type="button" disabled={exportBusy} onClick={()=>void exportRegister()}>
          {exportBusy?'Подготовка CSV…':'Скачать весь отфильтрованный реестр CSV'}
        </button>
        <p>Экспорт выгружает до 1000 записей в одном серверном срезе,
          а не только видимую страницу. При превышении лимита файл не скачивается.
          Только технические UUID, даты и состояния — без ПДн, денег и подписей.</p>
      </div>
      <p role="status">Записей на странице: {page.items.length}. Страница {history.length+1}.
        {page.nextCursor?' Есть следующая страница.':''}</p>
      {!page.items.length?<p>Заказ-наряды по этому фильтру не найдены.</p>:
      <div style={{overflowX:'auto'}}><table>
        <caption>Заказ-наряды своего филиала, новые сверху. Без ПДн и цен.</caption>
        <thead><tr><th scope="col">Заказ-наряд</th><th scope="col">Состояние</th>
          <th scope="col">Создан</th><th scope="col">Начало записи</th>
          <th scope="col">Подробнее</th></tr></thead>
        <tbody>{page.items.map(row=><tr key={row.workOrderId}>
          <th scope="row"><code>{row.workOrderId}</code></th>
          <td>{names[row.stage]}</td>
          <td>{new Date(row.createdAt).toLocaleString('ru-RU')}</td>
          <td>{new Date(row.startsAt).toLocaleString('ru-RU')}</td>
          <td><button type="button" onClick={()=>setSelected(
            prev=>prev===row.workOrderId?null:row.workOrderId)}>
            {selected===row.workOrderId?'Скрыть':'Сведения'}</button></td>
        </tr>)}</tbody>
      </table></div>}
      {selected&&page.items.some(x=>x.workOrderId===selected)&&(()=>{
        const row=page.items.find(x=>x.workOrderId===selected)!;
        return <section aria-label="Сведения о выбранном заказ-наряде">
          <h2>Сведения о заказ-наряде</h2>
          <p>Заказ: <code>{row.workOrderId}</code>. Состояние: {names[row.stage]}.</p>
          <p>Запись: <code>{row.appointmentId}</code>. Пост: <code>{row.bayId}</code>.</p>
          <p>Интервал: {new Date(row.startsAt).toLocaleString('ru-RU')} —
            {' '}{new Date(row.endsAt).toLocaleString('ru-RU')}.</p>
          <Link to={'/staff/auto-order-workspace?'+new URLSearchParams({
            org:org.trim(),loc:loc.trim(),order:row.workOrderId,
          })}>Открыть рабочее место наряда</Link>
          {' · '}
          <Link to={detailLink(row.workOrderId)}>Открыть услуги выбранного наряда</Link>
          <p>Стоимость, платежи, фото и документы доступны лишь в отдельных
            операционных экранах при наличии специальных полномочий.</p>
        </section>;
      })()}
      <nav aria-label="Страницы заказ-нарядов" style={{display:'flex',gap:'0.75rem'}}>
        {history.length>0&&<button type="button" onClick={()=>{
          setCursor(history[history.length-1]);setHistory(x=>x.slice(0,-1));
        }}>Предыдущая страница</button>}
        {page.nextCursor&&<button type="button" onClick={()=>{
          setHistory(x=>[...x,cursor]);setCursor(page.nextCursor);
        }}>Следующая страница</button>}
      </nav>
    </>}
    {exportMessage&&<p role="status">{exportMessage}</p>}
    <button type="button" onClick={()=>{setCursor(null);setHistory([]);
      setRevision(x=>x+1);}}>Обновить реестр</button>
  </section>;
}
