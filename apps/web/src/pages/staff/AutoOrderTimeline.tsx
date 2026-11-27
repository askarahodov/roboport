import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const actionNames:Record<string,string>={
  ORDER_CREATED:'Создан заказ-наряд',
  SERVICE_SELECTED:'Выбрана работа',
  INSPECTION_RECORDED:'Сохранён осмотр',
  INTAKE_ACT_CREATED:'Создана версия акта приёмки',
  INTAKE_ACKNOWLEDGED:'Клиент подтвердил акт',
  INTAKE_DISPUTED:'Клиент оспорил акт',
  ESTIMATE_PUBLISHED:'Опубликована версия сметы',
  ESTIMATE_APPROVED:'Клиент согласовал смету',
  ESTIMATE_DECLINED:'Клиент отклонил смету',
  EXECUTION_STARTED:'Начато выполнение работ',
  LABOR_RECORDED:'Учтена выполненная работа',
  EXECUTION_FINISHED:'Завершены работы',
  QC_PASS:'Контроль качества пройден',
  QC_FAIL:'Контроль качества не пройден',
  DIAGNOSTIC_RECORDED:'Записан результат диагностики',
  QC_REWORK_RECORDED:'Зафиксирована доработка после QC',
  COMPLEX_SELECTED:'Выбран комплекс работ',
  EXTRA_PUBLISHED:'Опубликована дополнительная смета',
  EXTRA_APPROVED:'Клиент согласовал дополнительные работы',
  EXTRA_DECLINED:'Клиент отклонил дополнительные работы',
  ASSIGNMENT_OFFERED:'Предложено назначение исполнителя',
  ASSIGNMENT_ACCEPTED:'Исполнитель принял назначение',
  ASSIGNMENT_DECLINED:'Исполнитель отклонил назначение',
  ASSIGNMENT_WITHDRAWN:'Предложение назначения отозвано',
  ASSIGNMENT_RELEASED:'Принятое назначение снято',
  RECOMMENDATION_CREATED:'Создана рекомендация по обслуживанию',
  RECOMMENDATION_WITHDRAWN:'Рекомендация отозвана',
  RECOMMENDATION_VISIT_BOOKED:'Создана запись по рекомендации',
  PREVIEW_SEALED:'Сформирован технический пакет',
  DOCUMENT_POSTED:'Техническое проведение',
  DOCUMENT_CLIENT_ACK:'Клиент подтвердил технический пакет',
  DOCUMENT_CLIENT_DISPUTE:'Клиент оспорил технический пакет',
  DOCUMENT_CLOSED_TECHNICAL:'Техническое закрытие',
};
type Entry={evidenceId:string;createdAt:string;action:string};
type Page={syntheticOnly:true;realMoney:false;releaseAllowed:false;
  workOrderId:string;items:Entry[];nextCursor:string|null};

function parse(raw:unknown,order:string):Page{
  if(!raw||typeof raw!=='object')throw Error('invalid timeline');
  const p=raw as Page;
  if(p.syntheticOnly!==true||p.realMoney!==false||p.releaseAllowed!==false||
    p.workOrderId!==order||!Array.isArray(p.items)||p.items.length>25||
    !(p.nextCursor===null||(typeof p.nextCursor==='string'&&
      p.nextCursor.length>0&&p.nextCursor.length<=256)))throw Error('invalid page');
  const seen=new Set<string>();
  for(const item of p.items){
    if(!item||typeof item.evidenceId!=='string'||!uuid.test(item.evidenceId)||
      typeof item.action!=='string'||!Object.prototype.hasOwnProperty.call(actionNames,item.action)||
      typeof item.createdAt!=='string'||Number.isNaN(Date.parse(item.createdAt))||
      seen.has(item.evidenceId))throw Error('invalid event');
    seen.add(item.evidenceId);
  }
  return p;
}

export function AutoOrderTimeline({lockedScope}:{lockedScope:{
  org:string;loc:string;order:string;
}}){
  const {org,loc,order}=lockedScope;
  const scope=org+':'+loc+':'+order;
  const [cursor,setCursor]=React.useState<string|null>(null);
  const [history,setHistory]=React.useState<Array<string|null>>([]);
  const [page,setPage]=React.useState<Page|null>(null);
  const [status,setStatus]=React.useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [revision,setRevision]=React.useState(0);
  const key=scope+':'+cursor+':'+revision;
  const [loaded,setLoaded]=React.useState('');
  React.useEffect(()=>{
    setCursor(null);setHistory([]);setPage(null);setLoaded('');
  },[scope]);
  React.useEffect(()=>{
    const controller=new AbortController();
    setPage(null);setLoaded('');setStatus('loading');
    const params=new URLSearchParams({limit:'25'});
    if(cursor)params.set('cursor',cursor);
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/orders/'+encodeURIComponent(order)+
      '/timeline?'+params;
    void fetch(path,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async response=>{
        if(controller.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw Error('timeline response');
        const result=parse(await response.json(),order);
        if(!controller.signal.aborted){setPage(result);setLoaded(key);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,cursor,revision]);
  return <section aria-label="Хронология заказ-наряда">
    <h2>История действий по заказ-наряду</h2>
    <p>События из первичных технических журналов. Не является бухгалтерской
      книгой, юридической подписью или подтверждением выдачи автомобиля.</p>
    {status==='loading'&&<p role="status">Проверка событий и доступа…</p>}
    {status==='denied'&&<p role="alert">Доступ к истории запрещён. Записи скрыты.</p>}
    {status==='error'&&<p role="alert">История недоступна. Обновите журнал.</p>}
    {status==='ready'&&page&&loaded===key&&<>
      <p role="status">Событий на странице: {page.items.length}.
        Страница {history.length+1}.</p>
      {!page.items.length?<p>Технических событий пока нет.</p>:
        <ol>{page.items.map(item=><li key={item.evidenceId}>
          <strong>{actionNames[item.action]}</strong>{' — '}
          <time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString('ru-RU')}</time>
          <small style={{display:'block'}}>Событие: <code>{item.evidenceId}</code></small>
        </li>)}</ol>}
      <nav aria-label="Страницы истории заказ-наряда">
        {history.length>0&&<button type="button" onClick={()=>{
          setCursor(history[history.length-1]);setHistory(x=>x.slice(0,-1));
        }}>Предыдущая страница истории</button>}
        {page.nextCursor&&<button type="button" onClick={()=>{
          setHistory(x=>[...x,cursor]);setCursor(page.nextCursor);
        }}>Следующая страница истории</button>}
      </nav>
    </>}
    <button type="button" onClick={()=>{setCursor(null);setHistory([]);
      setRevision(x=>x+1);}}>Обновить историю</button>
  </section>;
}
