import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Event={id:string;kind:'SIM_PAYMENT'|'SIM_REFUND';amountMinor:number;createdAt:string};
type Stock={skuId:string;quantity:number};
type Preview={id:string;revisionId:string;digest:string;createdAt:string;
  snapshot:Record<string,unknown>};
type DocumentEvent={id:string;action:'POSTED'|'CLIENT_ACK'|'CLIENT_DISPUTE'|'CLOSED_TECHNICAL';
  actorId:string;previewId:string;previewDigest:string;intakeActId:string;intakeDigest:string;
  createdAt:string};
type DocumentWorkflow={syntheticOnly:true;releaseAllowed:false;legalSignatureStatus:'NOT_IMPLEMENTED';
  status:'DRAFT'|'POSTED'|'CLIENT_ACKNOWLEDGED'|'CLIENT_DISPUTED'|'CLOSED_TECHNICAL';
  intakeAct:{id:string;version:number;digest:string;clientDecision:string|null}|null;
  events:DocumentEvent[];canReview:boolean;canClose:boolean};
type Snapshot={syntheticOnly:true;releaseAllowed:false;legalGatePassed:false;
  documentWorkflow?:DocumentWorkflow;
  currency:'RUB';approvedVersion:number|null;
  approvedBaseMinor:number|null;approvedExtraMinor:number;
  approvedExtraVersion:number|null;approvedExtraRevisionId:string|null;
  approvedTotalMinor:number|null;
  simulatedPaidMinor:number;simulatedRefundedMinor:number;simulatedDueMinor:number|null;
  events:Event[];outstandingReserves:Stock[];unreceivedMaterialCount:number;
  executionStatus:string;blockers:string[];canSealPreview:boolean;preview:Preview|null};
type Pending={path:string;body:string};

function parse(raw:unknown):Snapshot{
  if(!raw||typeof raw!=='object')throw new Error('invalid');
  const v=raw as Record<string,unknown>;
  if(v.syntheticOnly!==true||v.releaseAllowed!==false||v.legalGatePassed!==false||
    v.currency!=='RUB'||!Array.isArray(v.events)||v.events.length>100||
    !Array.isArray(v.outstandingReserves)||v.outstandingReserves.length>100||
    !Array.isArray(v.blockers)||!v.blockers.every(x=>typeof x==='string')||
    typeof v.canSealPreview!=='boolean'||typeof v.executionStatus!=='string'||
    typeof v.simulatedPaidMinor!=='number'||typeof v.simulatedRefundedMinor!=='number'){
    throw new Error('invalid demo state');
  }
  // Не показываем неполный или арифметически противоречивый финансовый итог.
  const minor=(value:unknown):value is number=>
    typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;
  const base=v.approvedBaseMinor,extra=v.approvedExtraMinor,total=v.approvedTotalMinor;
  const paid=v.simulatedPaidMinor,refunded=v.simulatedRefundedMinor;
  if((base!==null&&!minor(base))||!minor(extra)||
    (total!==null&&!minor(total))||
    (base===null?total!==null:total!==Number(base)+Number(extra))||
    !minor(paid)||!minor(refunded)||refunded>paid||
    (total!==null&&v.simulatedDueMinor!==Number(total)-paid+refunded)||
    (total===null&&v.simulatedDueMinor!==null)||
    (v.approvedExtraRevisionId===null?
      v.approvedExtraVersion!==null||extra!==0:
      typeof v.approvedExtraRevisionId!=='string'||
      !uuid.test(v.approvedExtraRevisionId)||
      !Number.isInteger(v.approvedExtraVersion)||
      Number(v.approvedExtraVersion)<1)){
    throw new Error('invalid reconciliation state');
  }
  // Older synthetic UI fixtures omit this field. The live dev endpoint
  // always includes it; never infer posting or legal release from omission.
  if(v.documentWorkflow!==undefined){
    const flow=v.documentWorkflow as DocumentWorkflow;
    if(!flow||flow.syntheticOnly!==true||flow.releaseAllowed!==false||
      flow.legalSignatureStatus!=='NOT_IMPLEMENTED'||
      !['DRAFT','POSTED','CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL'].includes(flow.status)||
      !Array.isArray(flow.events)||flow.events.length>4||
      typeof flow.canReview!=='boolean'||typeof flow.canClose!=='boolean'||
      !(flow.intakeAct===null||(uuid.test(flow.intakeAct.id)&&
        Number.isInteger(flow.intakeAct.version)&&
        /^[0-9a-f]{64}$/.test(flow.intakeAct.digest)))||
      !flow.events.every(e=>uuid.test(e.id)&&uuid.test(e.actorId)&&
        uuid.test(e.previewId)&&uuid.test(e.intakeActId)&&
        /^[0-9a-f]{64}$/.test(e.previewDigest)&&
        /^[0-9a-f]{64}$/.test(e.intakeDigest))){
      throw new Error('invalid document workflow');
    }
  }
  return v as Snapshot;
}

const reasons:Record<string,string>={
  LATEST_ESTIMATE_NOT_APPROVED:'Последняя смета не согласована клиентом',
  EXECUTION_OR_INDEPENDENT_QC_INCOMPLETE:'Работы или независимый QC ещё не завершены',
  MATERIAL_HANDOFF_OPEN:'Есть выдачи материалов без подтверждения получения',
  STOCK_RESERVATION_OPEN:'Есть неосвобождённый резерв склада',
  SIMULATED_BALANCE_OPEN:'Остаток условной сверки не равен нулю',
  ADDITIONAL_CONSENT_PENDING:'Дополнительная работа ожидает отдельного решения клиента',
  EXTRA_BASE_REVISION_MISMATCH:'Допработа относится не к действующей смете',
};

export function AutoDemoClose({role}:{role:'staff'|'client'}){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState(''),[order,setOrder]=React.useState('');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [loaded,setLoaded]=React.useState(''),[data,setData]=React.useState<Snapshot|null>(null);
  const [revision,setRevision]=React.useState(0),[busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const [kind,setKind]=React.useState<'SIM_PAYMENT'|'SIM_REFUND'>('SIM_PAYMENT');
  const [amount,setAmount]=React.useState('');
  const [sku,setSku]=React.useState(''),[qty,setQty]=React.useState('1');
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const valid=[org,loc,order].every(x=>uuid.test(x.trim()));
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())
    +'/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim())
    +'/demo-close';
  const ready=status==='ready'&&loaded===scope&&data!==null;
  React.useEffect(()=>{
    setStatus(valid?'loading':'idle');setLoaded('');setData(null);setPending(null);setMessage('');
    if(!valid)return;
    const controller=new AbortController();
    void fetch(base,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async res=>{
        if(controller.signal.aborted)return;
        if([401,403,404].includes(res.status)){setStatus('denied');return;}
        if(!res.ok)throw Error('unavailable');
        const state=parse(await res.json());
        if(!controller.signal.aborted){setLoaded(scope);setData(state);setStatus('ready');}
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,valid,revision,role]);

  async function execute(path:string,body:string){
    if(!ready||busy)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('csrf');
      const csrfValue:unknown=await csrf.json();
      if(!csrfValue||typeof csrfValue!=='object'||!('csrfToken' in csrfValue)||
        typeof csrfValue.csrfToken!=='string')throw Error('csrf');
      const res=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrfValue.csrfToken},body});
      if([401,403,404].includes(res.status)){
        setData(null);setStatus('denied');setPending(null);return;
      }
      if(res.status===409||res.status===422){
        setPending(null);setMessage(res.status===409?
          'Операция отклонена: нарушены условия сверки или повторён другой запрос. Перечитайте статус.':
          'Проверьте поля и сумму в копейках.');
        setRevision(n=>n+1);return;
      }
      if(!res.ok)throw Error('unknown');
      setPending(null);setRevision(n=>n+1);
    }catch{
      setPending({path,body});
      setMessage('Результат неизвестен. Повторите исходный запрос с прежним ключом или перечитайте журнал.');
    }finally{setBusy(false);}
  }

  function submit(path:string,payload:Record<string,unknown>){
    void execute(base+'/'+path,JSON.stringify({...payload,requestKey:crypto.randomUUID()}));
  }
  const numeric=Number(amount),qtyNumber=Number(qty);
  const validAmount=amount.trim()!==''&&Number.isSafeInteger(numeric)&&numeric>=1&&numeric<=1e12;
  const validQty=qty.trim()!==''&&Number.isSafeInteger(qtyNumber)&&qtyNumber>=1&&qtyNumber<=1e9;
  const active=ready&&!busy&&!pending;
  const workflow=data?.documentWorkflow;
  function documentAction(action:DocumentEvent['action']){
    if(!active||!data?.preview||!workflow?.intakeAct||
      workflow.intakeAct.clientDecision!=='ACKNOWLEDGED')return;
    if((role==='staff'&& !['POSTED','CLOSED_TECHNICAL'].includes(action))||
       (role==='client'&& !['CLIENT_ACK','CLIENT_DISPUTE'].includes(action)))return;
    submit('document-events',{
      action,previewId:data.preview.id,previewDigest:data.preview.digest,
      intakeActId:workflow.intakeAct.id,intakeDigest:workflow.intakeAct.digest,
    });
  }
  return <section aria-label="Сверка и пакет перед выдачей (dev)"
    style={{maxWidth:'76rem',margin:'2rem auto',overflowWrap:'anywhere'}}>
    <h1>{role==='client'?'Просмотр пакета заказ-наряда (dev)':'Сверка и пакет перед выдачей (dev)'}</h1>
    <p>Только искусственные данные. Условные события — НЕ платежи и НЕ кассовые чеки.
      Пакет не является актом выдачи; автомобиль нельзя выдавать по этому экрану.</p>
    <label>UUID организации <input aria-label="UUID организации" disabled={busy}
      value={org} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" disabled={busy}
      value={loc} onChange={e=>setLoc(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" disabled={busy}
      value={order} onChange={e=>setOrder(e.target.value)}/></label>
    {status==='idle'&&<p>Введите три UUID для проверки заказа.</p>}
    {status==='loading'&&<p role="status">Загрузка технической сверки…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<><p role="alert">Не удалось загрузить пакет.</p>
      <button type="button" onClick={()=>setRevision(n=>n+1)}>Повторить загрузку</button></>}
    {ready&&data&&<>
      <h2>Сверка сметы (RUB, копейки)</h2>
      <p>Версия базовой сметы: {data.approvedVersion??'нет'}
        {' '}· Основные работы: {data.approvedBaseMinor??'нет'}
        {' '}· Согласованные допработы: {data.approvedExtraMinor}
        {' '}({data.approvedExtraVersion===null?'нет':'версия '+data.approvedExtraVersion})
        {' '}· Итого по согласованным работам: {data.approvedTotalMinor??'нет'}
        {' '}· Условно внесено: {data.simulatedPaidMinor}
        {' '}· Условно возвращено: {data.simulatedRefundedMinor}
        {' '}· Остаток: {data.simulatedDueMinor??'нет'}</p>
      <p>Исполнение: {data.executionStatus} · Неполученные материалы:
        {' '}{data.unreceivedMaterialCount}</p>
      <h3>Неизменяемый журнал условных операций</h3>
      {data.events.length===0?<p>Условных операций пока нет.</p>:
        <ol>{data.events.map(e=><li key={e.id}>{e.kind}: {e.amountMinor} коп.</li>)}</ol>}
      {role==='staff'&&!data.preview&&<form onSubmit={e=>{
        e.preventDefault();if(active&&validAmount)submit('simulated-cash',{kind,amountMinor:numeric});
      }}>
        <h3>Условное движение (MANAGER)</h3>
        <label>Тип события <select aria-label="Тип события" value={kind}
          onChange={e=>setKind(e.target.value as 'SIM_PAYMENT'|'SIM_REFUND')}>
          <option value="SIM_PAYMENT">Условное внесение</option>
          <option value="SIM_REFUND">Условный возврат</option></select></label>
        <label>Сумма, коп. <input type="number" aria-label="Сумма, коп."
          min="1" max="1000000000000" step="1" value={amount}
          onChange={e=>setAmount(e.target.value)}/></label>
        <button type="submit" disabled={!active||!validAmount}>Записать условное движение</button>
      </form>}
      <h3>Незакрытые резервы</h3>
      {data.outstandingReserves.length===0?<p>Незакрытых резервов нет.</p>:
        <ul>{data.outstandingReserves.map(s=><li key={s.skuId}>
          {s.skuId}: {s.quantity} шт.</li>)}</ul>}
      {role==='staff'&&!data.preview&&data.outstandingReserves.length>0&&<form
        onSubmit={e=>{e.preventDefault();if(active&&uuid.test(sku)&&validQty)
          submit('unused-reserve-release',{skuId:sku,quantity:qtyNumber});}}>
        <h3>Освободить неиспользованный резерв (WAREHOUSE)</h3>
        <label>SKU резерва <select aria-label="SKU резерва" value={sku}
          onChange={e=>setSku(e.target.value)}>
          <option value="">Выберите SKU</option>
          {data.outstandingReserves.map(s=><option value={s.skuId} key={s.skuId}>{s.skuId}</option>)}
        </select></label>
        <label>Количество освобождения <input type="number" min="1" max="1000000000"
          aria-label="Количество освобождения" value={qty}
          onChange={e=>setQty(e.target.value)}/></label>
        <button type="submit" disabled={!active||!uuid.test(sku)||!validQty}>
          Освободить резерв</button>
      </form>}
      <h3>Проверка готовности к пакету</h3>
      {data.blockers.length?<ul>{data.blockers.map(b=><li key={b}>{reasons[b]??b}</li>)}</ul>:
        <p>Технические проверки пройдены. Юридическое разрешение на выдачу отсутствует.</p>}
      {role==='staff'&&data.canSealPreview&&!data.preview&&
        <button type="button" disabled={!active} onClick={()=>submit('seal-preview',{})}>
          Зафиксировать технический пакет (MANAGER)</button>}
      {data.preview&&<section aria-label="Неизменяемый технический пакет">
        <h3>Технический пакет зафиксирован</h3>
        <p>Номер документа: {data.preview.id}</p>
        <p>SHA-256: <code>{data.preview.digest}</code></p>
        <p>Снимок версии сметы, QC и сверки неизменяем. Нет юридической подписи,
          фискального чека или факта передачи автомобиля.</p>
      </section>}
      {workflow&&<section aria-label="Технический жизненный цикл документа">
        <h2>Проведение и закрытие — только технический DEV</h2>
        <p role="status">Статус документа: {workflow.status}.
          {' '}Даже CLOSED_TECHNICAL означает только закрытие тестового журнала,
          не юридическое закрытие ЗН или выдачу машины.</p>
        {workflow.intakeAct?<p>Акт приёмки №{workflow.intakeAct.version};
          {' '}отметка клиента: {workflow.intakeAct.clientDecision??'отсутствует'}.
          {' '}SHA-256 акта: <code>{workflow.intakeAct.digest}</code>.</p>:
          <p>Нет версии акта приёмки; техническое проведение недоступно.</p>}
        <h3>Неизменяемый журнал переходов</h3>
        {workflow.events.length===0?<p>Технических проводок документа нет.</p>:
          <ol>{workflow.events.map(event=><li key={event.id}>
            {event.action} · {new Date(event.createdAt).toLocaleString('ru-RU')}
            {' '}· автор: <code>{event.actorId}</code>
            {' '}· SHA-256 пакета: <code>{event.previewDigest}</code>
          </li>)}</ol>}
        {role==='staff'&&workflow.status==='DRAFT'&&
          <button type="button" disabled={!active||!data.preview||
              workflow.intakeAct?.clientDecision!=='ACKNOWLEDGED'}
            onClick={()=>documentAction('POSTED')}>
            Технически провести документ (MANAGER)</button>}
        {role==='client'&&workflow.canReview&&<>
          <p>Сверьте сумму, SHA-256 пакета и акт приёмки.
            Эта отметка — не электронная подпись, не согласие на выдачу и не платёж.</p>
          <button type="button" disabled={!active}
            onClick={()=>documentAction('CLIENT_ACK')}>
            Подтвердить просмотр пакета (не подпись)</button>
          <button type="button" disabled={!active}
            onClick={()=>documentAction('CLIENT_DISPUTE')}>
            Оспорить технический пакет</button>
        </>}
        {workflow.status==='CLIENT_DISPUTED'&&<p role="alert">
          Клиент оспорил пакет. Техническое закрытие запрещено;
          исправление требует нового разрешённого процесса и юридической проверки.</p>}
        {role==='staff'&&workflow.canClose&&
          <button type="button" disabled={!active}
            onClick={()=>documentAction('CLOSED_TECHNICAL')}>
            Технически закрыть журнал (НЕ выдавать автомобиль)</button>}
      </section>}
      <p role="note">Разрешение на выдачу: НЕТ. Юридический и security gate: НЕ ПРОЙДЕН.</p>
      <button type="button" disabled={busy}
        onClick={()=>setRevision(n=>n+1)}>Обновить сверку</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&ready&&<div role="group" aria-label="Повтор неизвестной операции">
      <button type="button" disabled={busy} onClick={()=>void execute(pending.path,pending.body)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setRevision(n=>n+1);}}>
        Перечитать сверку</button>
    </div>}
  </section>;
}
