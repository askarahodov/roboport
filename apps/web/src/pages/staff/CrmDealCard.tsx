import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const stages=['NEW','QUALIFIED','BOOKED','LOST'];
const lineage=['NOT_BOOKED','UNAVAILABLE','AMBIGUOUS','NO_ORDER','RESTRICTED','LINKED'];
const bookingNames:Record<string,string>={BOOKED:'Запланирована',RESCHEDULED:'Перенесена',CANCELED:'Отменена'};
const lineageNames:Record<string,string>={NOT_BOOKED:'Запись ещё не связана',UNAVAILABLE:'Связь с записью недоступна',
  AMBIGUOUS:'Неоднозначная связь. Наряд и суммы скрыты',NO_ORDER:'Заказ-наряд ещё не создан',
  RESTRICTED:'Доступ к наряду ограничен',LINKED:'Связан один заказ-наряд'};
const stageNames:Record<string,string>={NEW:'Новое',QUALIFIED:'Обработано',BOOKED:'Записан',LOST:'Отказ'};
const stepNames:Record<string,string>={services:'Услуги',inspection:'Осмотр',intakeAct:'Акт приёмки',
  estimate:'Смета',execution:'Работы',quality:'Контроль качества',technicalDocuments:'Документы выдачи'};
const stateNames:Record<string,string>={SELECTED:'Выбраны',EMPTY:'Не выбраны',RECORDED:'Записан',
  MISSING:'Нет',AWAITING_CLIENT:'Ожидает клиента',APPROVED:'Согласовано',REJECTED:'Отклонено',
  ACCEPTED:'Принято',ACKNOWLEDGED:'Подтверждено',DISPUTED:'Оспорено',RESTRICTED:'Доступ ограничен',FINISHED:'Завершены',
  IN_PROGRESS:'В работе',NOT_STARTED:'Не начаты',PASS:'Пройден',FAIL:'Не пройден',
  NOT_CHECKED:'Не проверено',DRAFT:'Черновик',PREVIEW_SEALED:'Пакет зафиксирован',
  POSTED:'Проведено',CLIENT_ACKNOWLEDGED:'Подтверждено клиентом',
  CLIENT_DISPUTED:'Оспорено клиентом',CLOSED_TECHNICAL:'Технически закрыто'};
const eventNames:Record<string,string>={SIM_PAYMENT:'Имитация оплаты',SIM_REFUND:'Имитация возврата',
  ADVANCE:'Аванс',PAYMENT:'Оплата',APPLY_ADVANCE:'Зачёт аванса',
  REFUND_ADVANCE:'Возврат аванса',REFUND_PAYMENT:'Возврат оплаты'};
const documentNames:Record<string,string>={POSTED:'Проведено',CLIENT_ACK:'Подтверждение клиента',
  CLIENT_DISPUTE:'Возражение клиента',CLOSED_TECHNICAL:'Технически закрыто'};
type DocumentEvent={id:string;action:string;createdAt:string};
type Event={id:string;kind:string;amountMinor:string;createdAt:string};
type Demo={paidMinor:string;refundedMinor:string;dueMinor:string|null;events:Event[]};
type Treasury={advancedMinor:string;paymentMinor:string;appliedMinor:string;refundedAdvanceMinor:string;
  refundedPaymentMinor:string;settledMinor:string;dueMinor:string|null;events:Event[]};
type Deal={syntheticOnly:true;realMoney:false;releaseAllowed:false;leadId:string;visitNumber:number;stage:string;
  lineage:string;booking:null|{appointmentId:string;startsAt:string;endsAt:string;status:string};
  workOrder:null|{workOrderId:string;steps:Record<string,string>;documentEvents:DocumentEvent[]|null};
  financeStatus:'VISIBLE'|'RESTRICTED'|'UNAVAILABLE';
  finance:null|{currency:'RUB';approvedTotalMinor:string|null;demoClose:Demo;treasury:Treasury}};
const minor=(n:unknown):n is string=>typeof n==='string'&&/^-?(0|[1-9][0-9]{0,17})$/.test(n);
const nullable=(n:unknown)=>n===null||minor(n);
const stamp=(n:unknown)=>typeof n==='string'&&Number.isFinite(Date.parse(n));
function events(raw:unknown,allowed:string[]):raw is Event[]{
  return Array.isArray(raw)&&raw.length<=100&&raw.every(e=>e&&uuid.test(e.id)&&
    allowed.includes(e.kind)&&minor(e.amountMinor)&&BigInt(e.amountMinor)>0n&&stamp(e.createdAt))&&
    new Set(raw.map(e=>e.id)).size===raw.length;
}
function parse(raw:unknown,leadId:string):Deal{
  if(!raw||typeof raw!=='object')throw Error('shape');
  const x=raw as Deal;
  if(x.syntheticOnly!==true||x.realMoney!==false||x.releaseAllowed!==false||x.leadId!==leadId||
    !Number.isSafeInteger(x.visitNumber)||x.visitNumber<1||!stages.includes(x.stage)||!lineage.includes(x.lineage)||
    !['VISIBLE','RESTRICTED','UNAVAILABLE'].includes(x.financeStatus))throw Error('shape');
  if(x.booking!==null&&(!x.booking||!uuid.test(x.booking.appointmentId)||!stamp(x.booking.startsAt)||
    !stamp(x.booking.endsAt)||!(x.booking.status in bookingNames)||
    Date.parse(x.booking.startsAt)>=Date.parse(x.booking.endsAt)))throw Error('booking');
  if(x.stage!=='BOOKED'&&(x.booking!==null||x.lineage!=='NOT_BOOKED'))throw Error('booking');
  if(x.lineage==='LINKED'){
    if(!x.booking||!x.workOrder||!uuid.test(x.workOrder.workOrderId)||!x.workOrder.steps||
      !Object.keys(stepNames).every(k=>typeof x.workOrder?.steps[k]==='string'))throw Error('order');
    const docs=x.workOrder.documentEvents;
    if(docs!==null&&(!Array.isArray(docs)||docs.length>4||
      !docs.every(d=>d&&uuid.test(d.id)&&Object.hasOwn(documentNames,d.action)&&stamp(d.createdAt))||
      new Set(docs.map(d=>d.id)).size!==docs.length))throw Error('document history');
    if((x.workOrder.steps.technicalDocuments==='RESTRICTED')!==(docs===null))throw Error('document history');
  }else if(x.workOrder!==null)throw Error('order');
  if(x.financeStatus==='VISIBLE'){
    const f=x.finance;
    if(!x.workOrder||!f||f.currency!=='RUB'||!nullable(f.approvedTotalMinor)||!f.demoClose||!f.treasury||
      ![f.demoClose.paidMinor,f.demoClose.refundedMinor,f.treasury.advancedMinor,f.treasury.paymentMinor,
        f.treasury.appliedMinor,f.treasury.refundedAdvanceMinor,f.treasury.refundedPaymentMinor,
        f.treasury.settledMinor].every(minor)||!nullable(f.demoClose.dueMinor)||!nullable(f.treasury.dueMinor)||
      !events(f.demoClose.events,['SIM_PAYMENT','SIM_REFUND'])||
      !events(f.treasury.events,['ADVANCE','PAYMENT','APPLY_ADVANCE','REFUND_ADVANCE','REFUND_PAYMENT']))throw Error('finance');
    const sum=(rows:Event[],kind:string)=>rows.filter(e=>e.kind===kind).reduce((n,e)=>n+BigInt(e.amountMinor),0n);
    const d=f.demoClose,t=f.treasury;
    if(sum(d.events,'SIM_PAYMENT')!==BigInt(d.paidMinor)||sum(d.events,'SIM_REFUND')!==BigInt(d.refundedMinor)||
      sum(t.events,'ADVANCE')!==BigInt(t.advancedMinor)||sum(t.events,'PAYMENT')!==BigInt(t.paymentMinor)||
      sum(t.events,'APPLY_ADVANCE')!==BigInt(t.appliedMinor)||sum(t.events,'REFUND_ADVANCE')!==BigInt(t.refundedAdvanceMinor)||
      sum(t.events,'REFUND_PAYMENT')!==BigInt(t.refundedPaymentMinor)||
      BigInt(t.paymentMinor)+BigInt(t.appliedMinor)-BigInt(t.refundedPaymentMinor)!==BigInt(t.settledMinor)||
      (f.approvedTotalMinor===null?(d.dueMinor!==null||t.dueMinor!==null):
        d.dueMinor===null||t.dueMinor===null||
        BigInt(f.approvedTotalMinor)-BigInt(d.paidMinor)+BigInt(d.refundedMinor)!==BigInt(d.dueMinor)||
        BigInt(f.approvedTotalMinor)-BigInt(t.settledMinor)!==BigInt(t.dueMinor)))throw Error('finance');
  }else if(x.finance!==null)throw Error('finance');
  return x;
}
function money(value:string|null){
  if(value===null)return 'Не согласовано';
  const n=BigInt(value),abs=n<0n?-n:n;
  return (n<0n?'−':'')+(abs/100n).toLocaleString('ru-RU')+','+(abs%100n).toString().padStart(2,'0')+' ₽';
}
function Journal({title,rows}:{title:string;rows:Event[]}){
  return <div><h4>{title}</h4>{rows.length===0?<p>Операций нет.</p>:<div style={{overflowX:'auto'}}>
    <table><caption>{title}</caption><thead><tr><th>Операция</th><th>Дата</th><th>Сумма</th><th>Первичная запись</th></tr></thead>
    <tbody>{rows.map(e=><tr key={e.id}><td>{eventNames[e.kind]}</td>
      <td>{new Date(e.createdAt).toLocaleString('ru-RU')}</td><td>{money(e.amountMinor)}</td>
      <td style={{overflowWrap:'anywhere'}}>{e.id}</td></tr>)}</tbody></table></div>}</div>;
}
export function CrmDealCard({base,leadId}:{base:string;leadId:string}){
  const [data,setData]=React.useState<Deal|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const request=React.useRef<AbortController|null>(null);
  React.useEffect(()=>{setData(null);setStatus('idle');return ()=>request.current?.abort();},[base,leadId]);
  async function load(){
    request.current?.abort();const controller=new AbortController();request.current=controller;
    setData(null);setStatus('loading');
    try{
      const response=await fetch(base+'/'+encodeURIComponent(leadId)+'/deal',
        {credentials:'include',cache:'no-store',signal:controller.signal});
      if(controller.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(!response.ok)throw Error('http');
      const parsed=parse(await response.json(),leadId);
      if(!controller.signal.aborted){setData(parsed);setStatus('ready');}
    }catch{if(!controller.signal.aborted)setStatus('error');}
  }
  return <section aria-label="Карточка сделки CRM">
    <h3>Карточка сделки</h3>
    <button type="button" disabled={status==='loading'} onClick={()=>void load()}>
      {status==='idle'?'Загрузить карточку сделки':'Обновить карточку сделки'}</button>
    {status==='loading'&&<p role="status">Чтение связанной первички…</p>}
    {status==='denied'&&<p role="alert">Доступ к сделке запрещён. Данные очищены.</p>}
    {status==='error'&&<p role="alert">Не удалось получить полную карточку. Обновите её.</p>}
    {data&&status==='ready'&&<>
      <p>Обращение №{data.visitNumber} · {stageNames[data.stage]}. {lineageNames[data.lineage]}.</p>
      {data.booking&&<dl><dt>Запись</dt><dd style={{overflowWrap:'anywhere'}}>{data.booking.appointmentId}</dd>
        <dt>Статус записи</dt><dd>{bookingNames[data.booking.status]}</dd>
        <dt>Текущее время (браузер)</dt><dd>{new Date(data.booking.startsAt).toLocaleString('ru-RU')} — {new Date(data.booking.endsAt).toLocaleString('ru-RU')}</dd></dl>}
      {data.workOrder&&<><h4>Заказ-наряд</h4><p style={{overflowWrap:'anywhere'}}>{data.workOrder.workOrderId}</p>
        <dl>{Object.entries(stepNames).map(([k,label])=><React.Fragment key={k}>
          <dt>{label}</dt><dd>{stateNames[data.workOrder!.steps[k]]??'Нет подтверждённого статуса'}</dd>
        </React.Fragment>)}</dl>
        {data.workOrder.documentEvents!==null&&<><h4>История документов</h4>
          {data.workOrder.documentEvents.length===0?<p>Событий проведения нет.</p>:<ol>
            {data.workOrder.documentEvents.map(e=><li key={e.id}>{documentNames[e.action]} ·
              {' '+new Date(e.createdAt).toLocaleString('ru-RU')} · <span style={{overflowWrap:'anywhere'}}>{e.id}</span></li>)}</ol>}</>}
      </>}
      {data.financeStatus==='RESTRICTED'&&<p>Финансовая история недоступна по документным правам.</p>}
      {data.finance&&<><h4>Расчёты — только синтетические</h4>
        <p>Два независимых журнала. Не складывать суммы. Не выручка, не реальная оплата.</p>
        <dl><dt>Согласованная сумма</dt><dd>{money(data.finance.approvedTotalMinor)}</dd>
          <dt>Остаток имитации закрытия</dt><dd>{money(data.finance.demoClose.dueMinor)}</dd>
          <dt>Зачтено в тестовом казначействе</dt><dd>{money(data.finance.treasury.settledMinor)}</dd>
          <dt>Остаток тестового казначейства</dt><dd>{money(data.finance.treasury.dueMinor)}</dd></dl>
        <Journal title="Имитация закрытия" rows={data.finance.demoClose.events}/>
        <Journal title="Тестовое казначейство" rows={data.finance.treasury.events}/>
      </>}
    </>}
  </section>;
}
