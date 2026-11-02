import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Event={id:string;kind:'SIM_PAYMENT'|'SIM_REFUND';amountMinor:number;createdAt:string};
type Stock={skuId:string;quantity:number};
type Preview={id:string;revisionId:string;digest:string;createdAt:string;
  snapshot:Record<string,unknown>};
type Snapshot={syntheticOnly:true;releaseAllowed:false;legalGatePassed:false;
  currency:'RUB';approvedVersion:number|null;approvedTotalMinor:number|null;
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
  return v as Snapshot;
}

const reasons:Record<string,string>={
  LATEST_ESTIMATE_NOT_APPROVED:'Последняя смета не согласована клиентом',
  EXECUTION_OR_INDEPENDENT_QC_INCOMPLETE:'Работы или независимый QC ещё не завершены',
  MATERIAL_HANDOFF_OPEN:'Есть выдачи материалов без подтверждения получения',
  STOCK_RESERVATION_OPEN:'Есть неосвобождённый резерв склада',
  SIMULATED_BALANCE_OPEN:'Остаток условной сверки не равен нулю',
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
      <p>Версия: {data.approvedVersion??'нет'} · Смета: {data.approvedTotalMinor??'нет'}
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
