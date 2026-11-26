import React from 'react';
import {Link,useSearchParams} from 'react-router-dom';

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
const repairStages=['ALL','DRAFT','IN_WORK','READY'] as const;
const documentStages=['ALL','RESTRICTED','DRAFT','PREVIEW_SEALED','POSTED','CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL'] as const;
const bookingStates=['ALL','SCHEDULED','CANCELED'] as const;
type RepairStage=typeof repairStages[number];
type DocumentStage=typeof documentStages[number];
type BookingState=typeof bookingStates[number];
const docNames:Record<DocumentStage,string>={ALL:'Все документы',RESTRICTED:'Недоступно по правам',
  DRAFT:'Не проведён',PREVIEW_SEALED:'Пакет сформирован',POSTED:'Технически проведён',
  CLIENT_ACKNOWLEDGED:'Подтверждён клиентом',CLIENT_DISPUTED:'Оспорен клиентом',CLOSED_TECHNICAL:'Технически закрыт'};
const bookingNames:Record<BookingState,string>={ALL:'Все записи',SCHEDULED:'Действующая',CANCELED:'Отменена'};
const validDay=(s:string)=>!s||(/^\d{4}-\d{2}-\d{2}$/.test(s)&&
  Number.isFinite(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s&&s.slice(0,4)!=='0000');
type Order={workOrderId:string;appointmentId:string;bayId:string;
  createdAt:string;startsAt:string;endsAt:string;stage:Exclude<Stage,'ALL'>;
  repairStage:Exclude<RepairStage,'ALL'>;documentStage:Exclude<DocumentStage,'ALL'>;
  bookingStatus:Exclude<BookingState,'ALL'>;localDay:string};
type Page={syntheticOnly:true;releaseAllowed:false;items:Order[];
  nextCursor:string|null};

function parse(raw:unknown,stage:Stage,repair:RepairStage,document:DocumentStage,
               booking:BookingState,fromDay:string,toDay:string):Page{
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
      !repairStages.slice(1).includes(row.repairStage)||!documentStages.slice(1).includes(row.documentStage)||
      !bookingStates.slice(1).includes(row.bookingStatus)||!row.localDay||!validDay(row.localDay)||
      (repair!=='ALL'&&row.repairStage!==repair)||(document!=='ALL'&&row.documentStage!==document)||
      (booking!=='ALL'&&row.bookingStatus!==booking)||(fromDay!==''&&row.localDay<fromDay)||
      (toDay!==''&&row.localDay>toDay)||
      row.stage!==(row.bookingStatus==='CANCELED'?'CANCELED':
        ['POSTED','CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL'].includes(row.documentStage)?
          row.documentStage:row.repairStage)||
      ids.has(row.workOrderId))throw Error('invalid row');
    ids.add(row.workOrderId);
  }
  return data;
}

export function AutoWorkOrderRegister(){
  const [query]=useSearchParams();
  const initial=(key:string)=>{const value=query.get(key)??'';return uuid.test(value)?value.toLowerCase():'';};
  const [org,setOrg]=React.useState(()=>initial('org'));
  const [loc,setLoc]=React.useState(()=>initial('loc'));
  const [stage,setStage]=React.useState<Stage>('ALL');
  const [repair,setRepair]=React.useState<RepairStage>('ALL');
  const [document,setDocument]=React.useState<DocumentStage>('ALL');
  const [booking,setBooking]=React.useState<BookingState>('ALL');
  const [fromDay,setFromDay]=React.useState(''),[toDay,setToDay]=React.useState('');
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
  const listController=React.useRef<AbortController|null>(null);
  const validPeriod=validDay(fromDay)&&validDay(toDay)&&(!fromDay||!toDay||fromDay<=toDay);
  const valid=uuid.test(org.trim())&&uuid.test(loc.trim())&&validPeriod;
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/orders';
  const scope=[org.trim(),loc.trim(),stage,applied,repair,document,booking,fromDay,toDay].join(':');
  const previousScope=React.useRef(scope);
  const effectiveCursor=previousScope.current===scope?cursor:null;
  const queryKey=scope+':'+effectiveCursor+':'+revision;
  currentScope.current=queryKey;
  function filters(){
    const params=new URLSearchParams({view:'workflow',stage,repairStage:repair,documentStage:document,bookingStatus:booking});
    if(applied)params.set('orderId',applied);
    if(fromDay)params.set('fromDay',fromDay);
    if(toDay)params.set('toDay',toDay);
    return params;
  }
  React.useEffect(()=>{
    exportController.current?.abort();
    setExportBusy(false);setExportMessage('');
    return ()=>exportController.current?.abort();
  },[queryKey]);
  React.useEffect(()=>{
    previousScope.current=scope;
    setCursor(null);setHistory([]);setPage(null);setSelected(null);setLoadedKey('');
  },[scope]);
  React.useEffect(()=>{
    setPage(null);setSelected(null);setLoadedKey('');
    if(!valid|| (applied!==''&&!uuid.test(applied))){setStatus('idle');return;}
    const controller=new AbortController();
    listController.current=controller;
    const current=()=>!controller.signal.aborted&&currentScope.current===queryKey;
    setStatus('loading');
    const params=filters();
    if(effectiveCursor)params.set('cursor',effectiveCursor);
    void fetch(base+'?'+params,{credentials:'include',cache:'no-store',
      signal:controller.signal}).then(async response=>{
      if(!current())return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=parse(await response.json(),stage,repair,document,booking,fromDay,toDay);
      if(current()){setPage(data);setLoadedKey(queryKey);setStatus('ready');}
    }).catch(()=>{if(current())setStatus('error');});
    return ()=>controller.abort();
  },[queryKey,valid]);

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
    const requested=queryKey;
    setExportBusy(true);setExportMessage('');
    const params=filters();
    try{
      const response=await fetch(base+'/export?'+params,{
        credentials:'include',cache:'no-store',signal:controller.signal,
      });
      if(controller.signal.aborted||currentScope.current!==requested)return;
      if([401,403,404].includes(response.status)){
        listController.current?.abort();setPage(null);setSelected(null);setLoadedKey('');setStatus('denied');
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
      юридическую подпись или физическую выдачу автомобиля. Без права чтения
      технических документов реестр и CSV показывают только операционный этап;
      фильтры проведения и закрытия не раскрывают скрытые документы.</p>
    <div style={{display:'flex',gap:'0.75rem',flexWrap:'wrap'}}>
      <label>UUID организации <input aria-label="UUID организации" value={org}
        onChange={e=>setOrg(e.target.value)}/></label>
      <label>UUID филиала <input aria-label="UUID филиала" value={loc}
        onChange={e=>setLoc(e.target.value)}/></label>
      <label>Общий статус (совместимость) <select aria-label="Состояние заказ-наряда" value={stage}
        onChange={e=>setStage(e.target.value as Stage)}>
        {stages.map(x=><option key={x} value={x}>{names[x]}</option>)}
      </select></label>
      <label>Этап ремонта <select value={repair} onChange={e=>setRepair(e.target.value as RepairStage)}>
        {repairStages.map(x=><option key={x} value={x}>{names[x]}</option>)}
      </select></label>
      <label>Состояние документа <select value={document} onChange={e=>setDocument(e.target.value as DocumentStage)}>
        {documentStages.map(x=><option key={x} value={x}>{docNames[x]}</option>)}
      </select></label>
      <label>Состояние записи <select value={booking} onChange={e=>setBooking(e.target.value as BookingState)}>
        {bookingStates.map(x=><option key={x} value={x}>{bookingNames[x]}</option>)}
      </select></label>
      <label>Дата записи с <input type="date" value={fromDay} onChange={e=>setFromDay(e.target.value)}/></label>
      <label>Дата записи по <input type="date" value={toDay} onChange={e=>setToDay(e.target.value)}/></label>
      <button type="button" onClick={()=>{setStage('ALL');setRepair('ALL');setDocument('ALL');setBooking('ALL');
        setFromDay('');setToDay('');setSearch('');setApplied('');setCursor(null);setHistory([]);setRevision(x=>x+1);}}>
        Сбросить все фильтры</button>
    </div>
    {!validPeriod&&<p role="alert">Проверьте диапазон дат записи.</p>}
    <p>Период по местной дате филиала, включительно. Этап ремонта, технический документ и запись независимы.</p>
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
        <thead><tr><th scope="col">Заказ-наряд</th><th scope="col">Этап ремонта</th><th scope="col">Документ</th>
          <th scope="col">Запись</th><th scope="col">Создан</th><th scope="col">Локальный день</th>
          <th scope="col">Подробнее</th></tr></thead>
        <tbody>{page.items.map(row=><tr key={row.workOrderId}>
          <th scope="row"><code>{row.workOrderId}</code></th>
          <td>{names[row.repairStage]}</td><td>{docNames[row.documentStage]}</td>
          <td>{bookingNames[row.bookingStatus]}</td>
          <td>{new Date(row.createdAt).toLocaleString('ru-RU')}</td><td>{row.localDay}</td>
          <td><button type="button" onClick={()=>setSelected(
            prev=>prev===row.workOrderId?null:row.workOrderId)}>
            {selected===row.workOrderId?'Скрыть':'Сведения'}</button></td>
        </tr>)}</tbody>
      </table></div>}
      {selected&&page.items.some(x=>x.workOrderId===selected)&&(()=>{
        const row=page.items.find(x=>x.workOrderId===selected)!;
        return <section aria-label="Сведения о выбранном заказ-наряде">
          <h2>Сведения о заказ-наряде</h2>
          <p>Заказ: <code>{row.workOrderId}</code>. Этап ремонта: {names[row.repairStage]}.
            Документ: {docNames[row.documentStage]}. Запись: {bookingNames[row.bookingStatus]}.</p>
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
