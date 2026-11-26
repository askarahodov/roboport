import React from 'react';
import {Link,useSearchParams} from 'react-router-dom';
import {AutoOrderServices} from './AutoOrderServices';
import {AutoWorkshopQc} from './AutoWorkshopQc';
import {AutoDiagnostics} from './AutoDiagnostics';
import {AutoAssignments} from './AutoAssignments';
import {AutoWorkExecution} from './AutoWorkExecution';
import {AutoIntakeAct} from '../shared/AutoIntakeAct';
import {AutoEstimateConsole} from '../shared/AutoEstimateConsole';
import {AutoDemoClose} from '../shared/AutoDemoClose';
import {AutoOrderTimeline} from './AutoOrderTimeline';
import {AutoServiceRecommendations} from '../shared/AutoServiceRecommendations';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Steps={
  services:'EMPTY'|'SELECTED';
  inspection:'MISSING'|'RECORDED';
  intakeAct:'MISSING'|'AWAITING_CLIENT'|'ACKNOWLEDGED'|'DISPUTED';
  estimate:'MISSING'|'AWAITING_CLIENT'|'APPROVED'|'DECLINED'|'RESTRICTED';
  execution:'NOT_STARTED'|'IN_PROGRESS'|'FINISHED';
  quality:'NOT_CHECKED'|'PASS'|'FAIL';
  technicalDocuments:'DRAFT'|'PREVIEW_SEALED'|'POSTED'|
    'CLIENT_ACKNOWLEDGED'|'CLIENT_DISPUTED'|'CLOSED_TECHNICAL'|'RESTRICTED';
};
type Stage=keyof Steps;
type Snapshot={workOrderId:string;syntheticOnly:true;realMoney:false;
  releaseAllowed:false;bookingCanceled:boolean;steps:Steps};
const stepNames:Record<Stage,string>={
  services:'Услуги',inspection:'Первичный осмотр',intakeAct:'Акт приёмки',
  estimate:'Смета',execution:'Исполнение',quality:'Независимый QC',
  technicalDocuments:'Технические документы',
};
const states:Record<Stage,readonly string[]>={
  services:['EMPTY','SELECTED'],
  inspection:['MISSING','RECORDED'],
  intakeAct:['MISSING','AWAITING_CLIENT','ACKNOWLEDGED','DISPUTED'],
  estimate:['MISSING','AWAITING_CLIENT','APPROVED','DECLINED','RESTRICTED'],
  execution:['NOT_STARTED','IN_PROGRESS','FINISHED'],
  quality:['NOT_CHECKED','PASS','FAIL'],
  technicalDocuments:['DRAFT','PREVIEW_SEALED','POSTED',
    'CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL','RESTRICTED'],
};
const stateNames:Record<string,string>={
  EMPTY:'Нет услуг',SELECTED:'Выбраны услуги',MISSING:'Не создан',
  RECORDED:'Осмотр сохранён',AWAITING_CLIENT:'Ожидает клиента',
  ACKNOWLEDGED:'Подтверждён клиентом',DISPUTED:'Оспорен',
  APPROVED:'Согласована',DECLINED:'Отклонена',NOT_STARTED:'Не начато',
  IN_PROGRESS:'В работе',FINISHED:'Работы завершены',
  NOT_CHECKED:'QC не проводился',PASS:'QC пройден',FAIL:'QC не пройден',
  RESTRICTED:'Недоступно по правам',
  DRAFT:'Черновик',PREVIEW_SEALED:'Технический пакет сформирован',
  POSTED:'Технически проведён',CLIENT_ACKNOWLEDGED:'Клиент подтвердил',
  CLIENT_DISPUTED:'Клиент оспорил',CLOSED_TECHNICAL:'Технически закрыт',
};
type Tab='services'|'inspection'|'intake'|'diagnostics'|'estimate'|
  'assignments'|'execution'|'qc'|'documents'|'history'|'recommendations';
const tabs:ReadonlyArray<{id:Tab;label:string;stage:Stage}>= [
  {id:'services',label:'Услуги',stage:'services'},
  {id:'inspection',label:'Осмотр',stage:'inspection'},
  {id:'intake',label:'Акт приёмки',stage:'intakeAct'},
  {id:'diagnostics',label:'Диагностика',stage:'inspection'},
  {id:'estimate',label:'Смета',stage:'estimate'},
  {id:'assignments',label:'Назначения',stage:'execution'},
  {id:'execution',label:'Работы и материалы',stage:'execution'},
  {id:'qc',label:'Контроль качества',stage:'quality'},
  {id:'documents',label:'Сверка и документы',stage:'technicalDocuments'},
  {id:'history',label:'История',stage:'technicalDocuments'},
  {id:'recommendations',label:'План следующего ТО',stage:'inspection'},
];

function parse(raw:unknown,expectedOrder:string):Snapshot{
  if(!raw||typeof raw!=='object')throw Error('invalid workspace');
  const r=raw as Record<string,unknown>;
  if(r.workOrderId!==expectedOrder||r.syntheticOnly!==true||r.realMoney!==false||
     r.releaseAllowed!==false||typeof r.bookingCanceled!=='boolean'||
     !r.steps||typeof r.steps!=='object')throw Error('invalid scope');
  const steps=r.steps as Record<string,unknown>;
  if(Object.keys(steps).length!==Object.keys(states).length||
     !Object.entries(states).every(([key,values])=>values.includes(String(steps[key]))))
    throw Error('invalid stage');
  return raw as Snapshot;
}

function ActivePanel({tab,lockedScope}:{tab:Tab;lockedScope:{org:string;loc:string;order:string}}){
  switch(tab){
    case 'services':return <AutoOrderServices lockedScope={lockedScope}/>;
    case 'inspection':return <AutoWorkshopQc lockedScope={lockedScope}/>;
    case 'intake':return <AutoIntakeAct role="staff" lockedScope={lockedScope}/>;
    case 'diagnostics':return <AutoDiagnostics lockedScope={lockedScope}/>;
    case 'estimate':return <AutoEstimateConsole role="manager" lockedScope={lockedScope}/>;
    case 'assignments':return <AutoAssignments lockedScope={lockedScope}/>;
    case 'execution':return <AutoWorkExecution lockedScope={lockedScope}/>;
    case 'qc':return <AutoWorkshopQc lockedScope={lockedScope}/>;
    case 'documents':return <AutoDemoClose role="staff" lockedScope={lockedScope}/>;
    case 'history':return <AutoOrderTimeline lockedScope={lockedScope}/>;
    case 'recommendations':return <AutoServiceRecommendations scope={lockedScope}/>;
  }
}

export function AutoWorkOrderWorkspace(){
  const [query,setQuery]=useSearchParams();
  const org=query.get('org')??'',loc=query.get('loc')??'',order=query.get('order')??'';
  const scope=[org,loc,order].join(':');
  const initialTab=tabs.find(t=>t.id===query.get('tab'))?.id??'services';
  const valid=[org,loc,order].every(x=>uuid.test(x));
  const [draftOrg,setDraftOrg]=React.useState(org);
  const [draftLoc,setDraftLoc]=React.useState(loc);
  const [draftOrder,setDraftOrder]=React.useState(order);
  const [active,setActive]=React.useState<Tab>('services');
  const [revision,setRevision]=React.useState(0);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [data,setData]=React.useState<Snapshot|null>(null);
  const [loaded,setLoaded]=React.useState('');
  const requestKey=scope+':'+revision;
  React.useEffect(()=>{
    setDraftOrg(org);setDraftLoc(loc);setDraftOrder(order);
    setActive(initialTab);
  },[scope,initialTab]);
  React.useEffect(()=>{
    setData(null);setLoaded('');
    if(!valid){setStatus('idle');return;}
    const controller=new AbortController();
    setStatus('loading');
    const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/orders/'+encodeURIComponent(order)+'/workspace';
    void fetch(path,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async response=>{
        if(controller.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw Error('workspace fetch');
        const body=parse(await response.json(),order);
        if(!controller.signal.aborted){setData(body);setLoaded(requestKey);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,revision,valid]);

  function apply(event:React.FormEvent){
    event.preventDefault();
    if(![draftOrg,draftLoc,draftOrder].every(x=>uuid.test(x.trim())))return;
    setQuery({org:draftOrg.trim().toLowerCase(),loc:draftLoc.trim().toLowerCase(),
      order:draftOrder.trim().toLowerCase()});
    setRevision(x=>x+1);
  }
  const visible=status==='ready'&&loaded===requestKey&&data!==null;
  return <section className="ui-page" aria-label="Рабочее место заказ-наряда"
    style={{maxWidth:'82rem',margin:'1rem auto',overflowWrap:'anywhere'}}>
    <h1>Рабочее место заказ-наряда — DEV</h1>
    <p>Все этапы одного тестового заказа на одной странице. Отдельные операции
      выполняются действующими ролями и <strong>повторно проверяются сервером</strong>.
      Это не бухгалтерский документ, не законная подпись и не выдача автомобиля.</p>
    <Link to={'/staff/auto-order-register?'+new URLSearchParams({org,loc}).toString()}>К реестру заказ-нарядов</Link>
    <form onSubmit={apply} style={{display:'flex',gap:'0.75rem',flexWrap:'wrap'}}>
      <label>UUID организации <input aria-label="UUID организации рабочего места"
        value={draftOrg} onChange={e=>setDraftOrg(e.target.value)}/></label>
      <label>UUID филиала <input aria-label="UUID филиала рабочего места"
        value={draftLoc} onChange={e=>setDraftLoc(e.target.value)}/></label>
      <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда рабочего места"
        value={draftOrder} onChange={e=>setDraftOrder(e.target.value)}/></label>
      <button type="submit" disabled={
        ![draftOrg,draftLoc,draftOrder].every(x=>uuid.test(x.trim()))}>
        Открыть заказ-наряд</button>
    </form>
    {status==='idle'&&<p>Введите три UUID или откройте заказ из реестра.</p>}
    {status==='loading'&&<p role="status">Проверка этапов и полномочий…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён. Проверьте действующую
      сессию, филиал и связь автомобиля с клиентом.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить этапы.
      Обновите сведения перед продолжением операций.</p>}
    {visible&&data&&<>
      {data.bookingCanceled&&<p role="alert">Текущая запись отменена.
        Не выполняйте новые работы без повторной проверки прав и состояния.</p>}
      <h2>Этапы выбранного заказ-наряда</h2>
      <p>Заказ: <code>{data.workOrderId}</code>. Цены и персональные данные
        не включены в обзор. Состояния — технические, не юридические.</p>
      <div style={{overflowX:'auto'}}><table>
        <caption>Серверная сводка этапов и документов выбранного наряда</caption>
        <thead><tr><th scope="col">Этап</th><th scope="col">Состояние</th></tr></thead>
        <tbody>{(Object.keys(stepNames) as Stage[]).map(key=><tr key={key}>
          <th scope="row">{stepNames[key]}</th>
          <td>{stateNames[data.steps[key]]}</td>
        </tr>)}</tbody>
      </table></div>
      <button type="button" onClick={()=>setRevision(n=>n+1)}>
        Обновить этапы с сервера</button>
      <h2>Операции по выбранному заказу</h2>
      <p>Выберите раздел ниже. Изменения внутри раздела становятся видны в сводке
        после «Обновить этапы». Открытие раздела не выдаёт новых полномочий. UUID заказа, филиала и организации
        закреплены: для другого документа вернитесь в реестр.</p>
      <nav aria-label="Разделы рабочего места" style={{display:'flex',flexWrap:'wrap',
        gap:'0.5rem'}}>
        {tabs.map(t=><button key={t.id} type="button"
          aria-current={active===t.id?'page':undefined}
          aria-pressed={active===t.id}
          onClick={()=>setActive(t.id)}>
          {t.label} · {stateNames[data.steps[t.stage]]}
        </button>)}
      </nav>
      <section aria-label="Активный раздел заказ-наряда" key={scope+':'+active}>
        <ActivePanel tab={active} lockedScope={{org,loc,order}}/>
      </section>
    </>}
  </section>;
}
