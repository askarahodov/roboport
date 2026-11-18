import React from 'react';
import {Link,useSearchParams} from 'react-router-dom';
import {AutoOrderServices} from './AutoOrderServices';
import {AutoWorkshopQc} from './AutoWorkshopQc';
import {AutoIntakeAct} from '../shared/AutoIntakeAct';
import {AutoDiagnostics} from './AutoDiagnostics';
import {AutoEstimateConsole} from '../shared/AutoEstimateConsole';
import {AutoWorkExecution} from './AutoWorkExecution';
import {AutoAssignments} from './AutoAssignments';
import {AutoDemoClose} from '../shared/AutoDemoClose';

/** Reuse the exact guarded operations; do not introduce a privileged aggregate endpoint. */
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const steps=['services','inspection','act','diagnostics','estimate','assignments','execution','settlement'] as const;
type Step=typeof steps[number];
const labels:Record<Step,string>={
  services:'1. Работы',inspection:'2. Приёмочный осмотр и QC',
  act:'3. Акт повреждений и комплектности',diagnostics:'4. Диагностика',
  estimate:'5. Смета и согласование',assignments:'6. Назначения механиков',
  execution:'7. Выполнение и материалы',settlement:'8. Условная сверка и документы',
};
type Item={workOrderId:string;stage:string;appointmentId:string;bayId:string;
  startsAt:string;endsAt:string};
type Reply={syntheticOnly:true;releaseAllowed:false;items:Item[];nextCursor:null|string};
function parse(raw:unknown,order:string):Item|null{
  if(!raw||typeof raw!=='object')throw Error('invalid scoped register');
  const v=raw as Reply;
  if(v.syntheticOnly!==true||v.releaseAllowed!==false||!Array.isArray(v.items)||
    v.items.length>1||v.nextCursor!==null)throw Error('invalid scoped page');
  const row=v.items[0];
  if(!row)return null;
  if(row.workOrderId!==order||!uuid.test(row.appointmentId)||!uuid.test(row.bayId)||
    typeof row.stage!=='string'||!row.stage||typeof row.startsAt!=='string'||
    typeof row.endsAt!=='string'||Number.isNaN(Date.parse(row.startsAt))||
    Number.isNaN(Date.parse(row.endsAt))||Date.parse(row.startsAt)>=Date.parse(row.endsAt))
    throw Error('invalid row');
  return row;
}

export function AutoOrderWorkspace(){
  const [query,setQuery]=useSearchParams();
  const org=query.get('org')??'',loc=query.get('loc')??'',order=query.get('order')??'';
  const requestedStep=query.get('step')??'services';
  const step:Step=steps.includes(requestedStep as Step)?requestedStep as Step:'services';
  const valid=[org,loc,order].every(v=>uuid.test(v));
  const scope=org+':'+loc+':'+order;
  const [revision,setRevision]=React.useState(0);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [item,setItem]=React.useState<Item|null>(null);
  const [verifiedScope,setVerifiedScope]=React.useState('');
  const [verifiedRevision,setVerifiedRevision]=React.useState(-1);
  React.useEffect(()=>{
    setItem(null);setVerifiedScope('');setStatus('idle');
    if(!valid)return;
    const abort=new AbortController();
    setStatus('loading');
    const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
      '/locations/'+encodeURIComponent(loc)+'/orders';
    const params=new URLSearchParams({orderId:order});
    void fetch(base+'?'+params,{credentials:'include',cache:'no-store',signal:abort.signal})
      .then(async response=>{
        if(abort.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw Error('register');
        const row=parse(await response.json(),order);
        if(abort.signal.aborted)return;
        if(!row){setStatus('denied');return;}
        setItem(row);setVerifiedScope(scope);setVerifiedRevision(revision);setStatus('ready');
      }).catch(()=>{if(!abort.signal.aborted)setStatus('error');});
    return ()=>abort.abort();
  },[scope,revision,valid]);
  const ready=status==='ready'&&verifiedScope===scope&&
    verifiedRevision===revision&&item!==null;
  const contextKey=scope+':'+step;
  const links=new URLSearchParams({org,loc,order});
  function changeStep(next:Step){
    const nextQuery=new URLSearchParams({org,loc,order,step:next});
    setQuery(nextQuery);
  }
  return <section aria-label="Рабочее место заказ-наряда" style={{
    maxWidth:'80rem',margin:'1.25rem auto',padding:'0 1rem',overflowWrap:'anywhere'}}>
    <h1>Заказ-наряд: единое рабочее место (dev)</h1>
    <p>Одна последовательность операций над выбранным заказом. Все действия выполняют
      существующие серверные правила соответствующих ролей. Это синтетические данные,
      не законная выдача автомобиля и не реальная оплата.</p>
    <p><Link to="/staff/auto-order-register">Вернуться к реестру</Link></p>
    {!valid&&<p role="alert">Не указан корректный UUID организации, филиала или заказ-наряда.
      Выберите наряд в реестре.</p>}
    {status==='loading'&&<p role="status">Проверяем права и актуальность заказ-наряда…</p>}
    {status==='denied'&&<p role="alert">Заказ-наряд недоступен: нет прав, подтверждения
      клиента или действующей связи с филиалом.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить доступ к заказ-наряду.
      Не выполняйте операции до повторной серверной проверки.</p>}
    {valid&&<button type="button" onClick={()=>setRevision(n=>n+1)}>
      Повторно проверить доступ и состояние</button>}
    {ready&&item&&<>
      <section aria-label="Текущий заказ" style={{margin:'1rem 0'}}>
        <h2>Заказ <code>{item.workOrderId}</code></h2>
        <p>Техническое состояние: <strong>{item.stage}</strong>.
          Пост: <code>{item.bayId}</code>. Запись: <code>{item.appointmentId}</code>.</p>
        <p>Интервал: {new Date(item.startsAt).toLocaleString('ru-RU')} —
          {' '}{new Date(item.endsAt).toLocaleString('ru-RU')}.</p>
        {item.stage==='CANCELED'&&<p role="alert">Запись отменена: операции будут
          дополнительно отклонены сервером. Не используйте старые результаты.</p>}
        <p>Юридическое закрытие и физическая выдача: <strong>ЗАПРЕЩЕНЫ</strong>.</p>
      </section>
      <nav aria-label="Этапы заказ-наряда" style={{
        display:'flex',flexWrap:'wrap',gap:'0.5rem',margin:'1rem 0'}}>
        {steps.map(code=><button key={code} type="button" aria-current={step===code?'step':undefined}
          disabled={step===code} onClick={()=>changeStep(code)}>{labels[code]}</button>)}
      </nav>
      <section key={contextKey} aria-label={'Операция: '+labels[step]}>
        {step==='services'&&<AutoOrderServices/>}
        {step==='inspection'&&<AutoWorkshopQc/>}
        {step==='act'&&<AutoIntakeAct role="staff"/>}
        {step==='diagnostics'&&<AutoDiagnostics/>}
        {step==='estimate'&&<AutoEstimateConsole role="manager"/>}
        {step==='assignments'&&<AutoAssignments/>}
        {step==='execution'&&<AutoWorkExecution/>}
        {step==='settlement'&&<AutoDemoClose role="staff"/>}
      </section>
      <p>Каждый этап повторно проверяет полномочия. Изменение данных в одном
        разделе не гарантирует доступность следующего до обновления его состояния.
        {' '}<Link to={'/staff/auto-order-services?'+links}>Открыть услуги отдельно</Link>.
      </p>
    </>}
  </section>;
}
