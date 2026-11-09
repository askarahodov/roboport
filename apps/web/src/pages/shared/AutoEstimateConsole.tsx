import React from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { AutoAdditionalWork } from './AutoAdditionalWork';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Qualification='GENERAL'|'DIAGNOSTIC'|'MASTER';
type PricingMode='MANUAL'|'NORM_HOUR'|'WORK_FACTORED';
type Line={serviceId:string;code:string;title:string;durationMinutes:number;amountMinor:number;
  pricingMethod?:'NORM_HOUR'|'NORM_HOUR_FACTORED';qualificationCode?:Qualification;
  rateMinorPerHour?:number;quantity?:number;coefficientBps?:number;discountBps?:number};
type Revision={id:string;version:number;totalMinor:number;currency:'RUB';digest:string;
  lines:Line[];decision:'APPROVED'|'DECLINED'|null;createdAt:string;decidedAt:string|null;
  clarificationId?:string|null;clarificationReason?:'PRICE'|'WORK_SCOPE'|'TIMING'|'OTHER'|null;
  clarificationResolution?:'EXPLAINED'|'NEW_VERSION_REQUIRED'|null;
  diagnostics?:{serviceId:string;findingCode:string;severityCode:string}[]};
type Estimates={items:Revision[];latestVersion:number;syntheticOnly:true;releaseAllowed:false};
type Service={id:string;code:string;title:string;durationMinutes:number};
type Pending={path:string;body:string};

function validPricedLine(line:Record<string,unknown>):boolean{
  if(!('pricingMethod' in line))return true; // Pre-existing manual price.
  if(!['NORM_HOUR','NORM_HOUR_FACTORED'].includes(String(line.pricingMethod))||
    !['GENERAL','DIAGNOSTIC','MASTER'].includes(String(line.qualificationCode))||
    typeof line.rateMinorPerHour!=='number'||!Number.isSafeInteger(line.rateMinorPerHour)||
    line.rateMinorPerHour<0||line.rateMinorPerHour>1e10||
    typeof line.durationMinutes!=='number'||!Number.isInteger(line.durationMinutes)||
    line.durationMinutes<5||line.durationMinutes>1440||
    typeof line.amountMinor!=='number'||!Number.isSafeInteger(line.amountMinor)||
    line.amountMinor<0||line.amountMinor>1e12)return false;
  if(line.pricingMethod==='NORM_HOUR'){
    return !('quantity' in line)&&!('coefficientBps' in line)&&
      !('discountBps' in line)&&line.amountMinor===
      Math.floor((line.durationMinutes*line.rateMinorPerHour+30)/60);
  }
  const {quantity,coefficientBps,discountBps}=line;
  if(typeof quantity!=='number'||!Number.isInteger(quantity)||quantity<1||quantity>20||
     typeof coefficientBps!=='number'||!Number.isInteger(coefficientBps)||
     coefficientBps<10000||coefficientBps>30000||
     typeof discountBps!=='number'||!Number.isInteger(discountBps)||
     discountBps<0||discountBps>2500)return false;
  const numerator=BigInt(line.durationMinutes)*BigInt(line.rateMinorPerHour)*
    BigInt(quantity)*BigInt(coefficientBps)*BigInt(10000-discountBps);
  const expected=(numerator+3000000000n)/6000000000n;
  return expected<=1000000000000n&&BigInt(line.amountMinor)===expected;
}

function parseEstimate(raw:unknown):Estimates{
  if(!raw||typeof raw!=='object')throw Error('invalid estimate');
  const value=raw as Record<string,unknown>;
  if(!Array.isArray(value.items)||value.items.length>30||
    typeof value.latestVersion!=='number'||value.syntheticOnly!==true||
    value.releaseAllowed!==false||!value.items.every((row:unknown)=>{
      if(!row||typeof row!=='object')return false;
      const e=row as Record<string,unknown>;
      return typeof e.id==='string'&&uuid.test(e.id)&&
        typeof e.version==='number'&&Number.isInteger(e.version)&&
        typeof e.totalMinor==='number'&&Number.isSafeInteger(e.totalMinor)&&
        ['APPROVED','DECLINED',null].includes(e.decision as null)&&
        typeof e.digest==='string'&&e.digest.length===64&&
        (e.clarificationId===undefined||e.clarificationId===null||
          (typeof e.clarificationId==='string'&&uuid.test(e.clarificationId)))&&
        (e.clarificationReason===undefined||e.clarificationReason===null||
          ['PRICE','WORK_SCOPE','TIMING','OTHER'].includes(String(e.clarificationReason)))&&
        (e.clarificationResolution===undefined||e.clarificationResolution===null||
          ['EXPLAINED','NEW_VERSION_REQUIRED'].includes(String(e.clarificationResolution)))&&
        (e.diagnostics===undefined||(Array.isArray(e.diagnostics)&&e.diagnostics.length<=50&&
          e.diagnostics.every((f:unknown)=>f!==null&&typeof f==='object'&&
            'serviceId' in f&&typeof f.serviceId==='string'&&uuid.test(f.serviceId)&&
            'findingCode' in f&&['NO_FAULT','MAINTENANCE_DUE','FAULT_CONFIRMED','FURTHER_CHECK'].includes(String(f.findingCode))&&
            'severityCode' in f&&['ROUTINE','SOON','URGENT'].includes(String(f.severityCode)))))&&
        Array.isArray(e.lines)&&e.lines.length<=50&&
        e.lines.every((line:unknown)=>line!==null&&typeof line==='object'&&
          'code' in line&&typeof line.code==='string'&&
          'title' in line&&typeof line.title==='string'&&
          'amountMinor' in line&&typeof line.amountMinor==='number'&&
          validPricedLine(line as Record<string,unknown>));
    }))throw Error('invalid estimate');
  return value as Estimates;
}

function parseServices(raw:unknown):Service[]{
  if(!raw||typeof raw!=='object'||!('items' in raw)||!Array.isArray(raw.items)||
    raw.items.length>50||!raw.items.every((s:unknown)=>s!==null&&typeof s==='object'
      &&'id' in s&&typeof s.id==='string'&&uuid.test(s.id)
      &&'code' in s&&typeof s.code==='string'
      &&'title' in s&&typeof s.title==='string'
      &&'durationMinutes' in s&&typeof s.durationMinutes==='number'))throw Error('services');
  return raw.items as Service[];
}

export function AutoEstimateConsole({role}:{role:'manager'|'client'}){
  const [query]=useSearchParams();
  const [extraOpen,setExtraOpen]=React.useState(false);
  const [org,setOrg]=React.useState(query.get('org')??'');
  const [loc,setLoc]=React.useState(query.get('loc')??'');
  const [order,setOrder]=React.useState(query.get('order')??'');
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [scopeLoaded,setScopeLoaded]=React.useState('');
  const [data,setData]=React.useState<Estimates|null>(null);
  const [services,setServices]=React.useState<Service[]>([]);
  const [prices,setPrices]=React.useState<Record<string,string>>({});
  const [pricingMode,setPricingMode]=React.useState<PricingMode>('MANUAL');
  const [rates,setRates]=React.useState<Record<string,string>>({});
  const [qualifications,setQualifications]=React.useState<Record<string,Qualification>>({});
  const [quantities,setQuantities]=React.useState<Record<string,string>>({});
  const [coefficients,setCoefficients]=React.useState<Record<string,string>>({});
  const [discounts,setDiscounts]=React.useState<Record<string,string>>({});
  const [revision,setRevision]=React.useState(0);
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const [reason,setReason]=React.useState<'PRICE'|'WORK_SCOPE'|'TIMING'|'OTHER'>('PRICE');
  const valid=[org,loc,order].every(v=>uuid.test(v.trim()));
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())
    +'/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim());
  const ready=status==='ready'&&scopeLoaded===scope&&data!==null;

  React.useEffect(()=>{
    setStatus(valid?'loading':'idle');setData(null);setServices([]);
    setScopeLoaded('');setMessage('');setPending(null);setPrices({});
    setExtraOpen(false);
    setRates({});setQualifications({});
    setQuantities({});setCoefficients({});setDiscounts({});
    if(!valid)return;
    const controller=new AbortController();
    const routes=[base+'/estimates'];
    if(role==='manager')routes.push(base+'/services');
    void Promise.all(routes.map(path=>fetch(path,{credentials:'include',
      cache:'no-store',signal:controller.signal}))).then(async responses=>{
        if(controller.signal.aborted)return;
        if(responses.some(res=>[401,403,404].includes(res.status))){
          setStatus('denied');return;
        }
        if(responses.some(res=>!res.ok))throw Error('request');
        const payloads:unknown[]=await Promise.all(responses.map(res=>res.json()));
        const result=parseEstimate(payloads[0]);
        const selected=role==='manager'?parseServices(payloads[1]):[];
        if(controller.signal.aborted)return;
        setData(result);setServices(selected);setScopeLoaded(scope);setStatus('ready');
      }).catch(()=>{if(!controller.signal.aborted)setStatus('error');});
    return ()=>controller.abort();
  },[scope,role,revision,valid]);

  async function send(path:string,body:string){
    if(!ready||busy)return;
    setBusy(true);setMessage('');
    try {
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',cache:'no-store',credentials:'include'});
      if(!csrf.ok)throw Error('csrf');
      const response:unknown=await csrf.json();
      if(!response||typeof response!=='object'||!('csrfToken' in response)||
          typeof response.csrfToken!=='string')throw Error('csrf');
      const result=await fetch(path,{method:'POST',cache:'no-store',credentials:'include',
        headers:{'Content-Type':'application/json','X-CSRF-Token':response.csrfToken},body});
      if([401,403,404].includes(result.status)){
        setStatus('denied');setData(null);setServices([]);setPending(null);return;
      }
      if(!result.ok && result.status!==409 && result.status!==422)throw Error('unknown');
      setPending(null);
      if(!result.ok)setMessage(result.status===409?
        'Конфликт версии или подтверждения. Перечитайте последнюю смету.':
        'Проверьте поля сметы.');
      setRevision(n=>n+1);
    }catch{
      setPending({path,body});
      setMessage('Результат операции неизвестен. Повторите тот же запрос с прежним ключом или перечитайте версии.');
    }finally{setBusy(false);}
  }

  const latest=data?.items[data.items.length-1];
  function computedAmount(s:Service):number{
    const raw=pricingMode==='MANUAL'?(prices[s.id]??''):(rates[s.id]??'');
    if(!/^(0|[1-9][0-9]*)$/.test(raw))return NaN;
    const value=Number(raw);
    if(!Number.isSafeInteger(value))return NaN;
    if(pricingMode==='MANUAL')return value<=1e12?value:NaN;
    if(value>1e10||!Number.isInteger(s.durationMinutes)||
      s.durationMinutes<5||s.durationMinutes>1440)return NaN;
    if(pricingMode==='NORM_HOUR')
      return Math.floor((s.durationMinutes*value+30)/60);
    const quantityRaw=quantities[s.id]??'1';
    const coeffRaw=coefficients[s.id]??'10000';
    const discountRaw=discounts[s.id]??'0';
    if([quantityRaw,coeffRaw,discountRaw].some(x=>!/^(0|[1-9][0-9]*)$/.test(x)))
      return NaN;
    const quantity=Number(quantityRaw),coeff=Number(coeffRaw),
      discount=Number(discountRaw);
    if(!Number.isInteger(quantity)||quantity<1||quantity>20||
      !Number.isInteger(coeff)||coeff<10000||coeff>30000||
      !Number.isInteger(discount)||discount<0||discount>2500)return NaN;
    const amount=(BigInt(s.durationMinutes)*BigInt(value)*BigInt(quantity)*
      BigInt(coeff)*BigInt(10000-discount)+3000000000n)/6000000000n;
    return amount<=1000000000000n?Number(amount):NaN;
  }
  const amounts=services.map(computedAmount);
  const canPublish=role==='manager'&&ready&&!busy&&!pending&&services.length>0&&
    !(latest?.clarificationId&&!latest.clarificationResolution)&&
    !data?.items.some(item=>item.decision!==null)&&
    amounts.every(amount=>Number.isSafeInteger(amount)&&amount>=0&&amount<=1e12)&&
    amounts.reduce((sum,amount)=>sum+amount,0)<=1e12;
  return <section aria-label="Автомобильные сметы (dev)"
    style={{maxWidth:'76rem',margin:'2rem auto'}}>
    <h1>{role==='manager'?'Сметы заказ-наряда (dev)':'Согласование сметы (dev)'}</h1>
    <p>Только синтетические данные. Решение не является юридической электронной подписью,
      оплатой или разрешением на выдачу.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      disabled={busy} onChange={e=>setOrg(e.target.value)}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      disabled={busy} onChange={e=>setLoc(e.target.value)}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" value={order}
      disabled={busy} onChange={e=>setOrder(e.target.value)}/></label>
    {status==='idle'&&<p>Укажите организацию, филиал и заказ-наряд.</p>}
    {status==='loading'&&<p role="status">Загрузка версий сметы…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<p role="alert">Не удалось прочитать смету.</p>}
    {ready&&data&&<>
      <h2>История смет</h2>
      {data.items.length===0?<p>Опубликованных версий пока нет.</p>:
        <ol>{data.items.map(item=><li key={item.id}>
          <h3>Версия {item.version}: {item.totalMinor} коп. (RUB)</h3>
          <p>Решение: {item.decision??(item.version===data.latestVersion?
            'ОЖИДАЕТ РЕШЕНИЯ':'ЗАМЕНЕНА НОВОЙ ВЕРСИЕЙ')}</p>
          {item.clarificationId&&<p>Уточнение: {item.clarificationReason} ·
            {' '}{item.clarificationResolution??'ОЖИДАЕТ ОТВЕТА МЕНЕДЖЕРА'}</p>}
          <p>SHA-256 снимка: <code>{item.digest}</code></p>
          {item.diagnostics&&item.diagnostics.length>0&&<section aria-label={'Диагностика версии '+item.version}>
            <h4>Диагностическое заключение, включённое в эту версию</h4>
            <ul>{item.diagnostics.map(f=><li key={f.serviceId}>
              {item.lines.find(line=>line.serviceId===f.serviceId)?.code??f.serviceId}:
              {' '}{f.findingCode} · {f.severityCode}
            </li>)}</ul>
          </section>}
          <ul>{item.lines.map(line=><li key={line.serviceId}>
            {line.code} — {line.title}: {line.amountMinor} коп.
            {line.pricingMethod==='NORM_HOUR'&&<p>
              Расчёт по нормо-часу: {line.durationMinutes} мин. ×
              {' '}{line.rateMinorPerHour} коп./час · категория {line.qualificationCode}.
              Округление до копейки (0,5 вверх).
            </p>}
            {line.pricingMethod==='NORM_HOUR_FACTORED'&&<p>
              Расчёт работ с коэффициентом: {line.durationMinutes} мин. ×
              {' '}{line.rateMinorPerHour} коп./час · категория {line.qualificationCode};
              {' '}кратность {line.quantity} · коэффициент {line.coefficientBps}/10000
              {' '}· скидка {line.discountBps}/100 %.
              Все модификаторы входят в согласованную неизменяемую версию.
            </p>}
          </li>)}</ul>
          {role==='client'&&item.version===data.latestVersion&&!item.decision&&!pending&&
            <div>
            {!item.clarificationId&&<><label>Причина запроса уточнения
              <select aria-label="Причина уточнения сметы" value={reason}
                onChange={e=>setReason(e.target.value as typeof reason)}>
                <option value="PRICE">Цена работ</option>
                <option value="WORK_SCOPE">Состав работ</option>
                <option value="TIMING">Сроки</option>
                <option value="OTHER">Другое</option>
              </select></label>
              <button type="button" disabled={busy} onClick={()=>{
                void send(base+'/estimates/clarification',JSON.stringify({
                  revisionId:item.id,requestKey:crypto.randomUUID(),reasonCode:reason}));
              }}>Запросить уточнение версии {item.version}</button></>}
            {(!item.clarificationId||item.clarificationResolution==='EXPLAINED')&&
            <><button type="button" disabled={busy} onClick={()=>{
              void send(base+'/estimates/decision',JSON.stringify({
                revisionId:item.id,requestKey:crypto.randomUUID(),decision:'APPROVED'}));
            }}>Согласовать версию {item.version}</button>
            <button type="button" disabled={busy} onClick={()=>{
              void send(base+'/estimates/decision',JSON.stringify({
                revisionId:item.id,requestKey:crypto.randomUUID(),decision:'DECLINED'}));
            }}>Отклонить версию {item.version}</button></>}
            {item.clarificationResolution==='NEW_VERSION_REQUIRED'&&
              <p>Менеджер подготовит новую версию. Старую согласовать нельзя.</p>}
            </div>}
          {role==='manager'&&item.version===data.latestVersion&&item.clarificationId&&
            !item.clarificationResolution&&!pending&&<div>
              <button type="button" disabled={busy} onClick={()=>{
                void send(base+'/estimates/clarification/resolve',JSON.stringify({
                  revisionId:item.id,requestKey:crypto.randomUUID(),
                  resolutionCode:'EXPLAINED'}));
              }}>Ответить: пояснение предоставлено</button>
              <button type="button" disabled={busy} onClick={()=>{
                void send(base+'/estimates/clarification/resolve',JSON.stringify({
                  revisionId:item.id,requestKey:crypto.randomUUID(),
                  resolutionCode:'NEW_VERSION_REQUIRED'}));
              }}>Ответить: подготовить новую версию</button>
            </div>}
        </li>)}</ol>}
      {latest?.decision==='APPROVED'&&<section aria-label="Новые работы после сметы">
        <button type="button" aria-expanded={extraOpen}
          onClick={()=>setExtraOpen(open=>!open)}>
          {extraOpen?'Скрыть дополнительные работы':'Открыть дополнительные работы'}
        </button>
        {extraOpen&&<AutoAdditionalWork role={role} baseRevisionId={latest.id}
          baseServiceIds={latest.lines.map(line=>line.serviceId)}
          org={org.trim()} loc={loc.trim()} order={order.trim()}/>}
      </section>}
      {role==='manager'&&services.length>0&&!data.items.some(item=>item.decision!==null)&&
        <form onSubmit={event=>{
          event.preventDefault();if(!canPublish)return;
          void send(base+'/estimates',JSON.stringify({
            requestKey:crypto.randomUUID(),expectedVersion:data.latestVersion,
            ...(pricingMode==='MANUAL'?{
              prices:services.map(service=>({serviceId:service.id,
                amountMinor:Number(prices[service.id])})),
            }:pricingMode==='NORM_HOUR'?{
              laborRates:services.map(service=>({serviceId:service.id,
                qualificationCode:qualifications[service.id]??'GENERAL',
                rateMinorPerHour:Number(rates[service.id])})),
            }:{
              workRates:services.map(service=>({serviceId:service.id,
                qualificationCode:qualifications[service.id]??'GENERAL',
                rateMinorPerHour:Number(rates[service.id]),
                quantity:Number(quantities[service.id]??'1'),
                coefficientBps:Number(coefficients[service.id]??'10000'),
                discountBps:Number(discounts[service.id]??'0')})),
            }),
          }));
        }}>
          <h2>Новая неизменяемая версия</h2>
          <label>Способ расчёта стоимости
            <select aria-label="Способ расчёта стоимости" value={pricingMode}
              onChange={e=>setPricingMode(e.target.value as PricingMode)}>
              <option value="MANUAL">Ручная стоимость услуги</option>
              <option value="NORM_HOUR">По нормо-часу и тарифной категории</option>
              <option value="WORK_FACTORED">Нормо-час × кратность × коэффициент − скидка</option>
            </select>
          </label>
          {pricingMode!=='MANUAL'&&<p>Основа расчёта — плановые минуты выбранной
            услуги. Категория — тарифная предпосылка, не подтверждение квалификации
            сотрудника. Сервер пересчитывает каждую сумму и сохраняет ставку в версии.
            {pricingMode==='WORK_FACTORED'&&' Кратность 1–20, коэффициент 1,00–3,00 и скидка до 25% — ограничения синтетического контура.'}
          </p>}
          {services.map(s=><div key={s.id}>
            {pricingMode==='MANUAL'?<label>{s.code} — {s.title}, стоимость (коп.)
              <input type="number" min="0" max="1000000000000" step="1"
                aria-label={'Стоимость услуги '+s.code}
                value={prices[s.id]??''} onChange={e=>setPrices(prev=>({
                  ...prev,[s.id]:e.target.value,
                }))}/></label>:<>
              <p>{s.code} — {s.title} · план {s.durationMinutes} мин.</p>
              <label>Категория тарифа {s.code}
                <select aria-label={'Категория тарифа '+s.code}
                  value={qualifications[s.id]??'GENERAL'}
                  onChange={e=>setQualifications(prev=>({
                    ...prev,[s.id]:e.target.value as Qualification,
                  }))}>
                  <option value="GENERAL">Общий</option>
                  <option value="DIAGNOSTIC">Диагностика</option>
                  <option value="MASTER">Мастер</option>
                </select>
              </label>
              <label>Ставка нормо-часа {s.code}, коп./час
                <input aria-label={'Ставка нормо-часа '+s.code} type="number"
                  min="0" max="10000000000" step="1" value={rates[s.id]??''}
                  onChange={e=>setRates(prev=>({...prev,[s.id]:e.target.value}))}/>
              </label>
              {pricingMode==='WORK_FACTORED'&&<>
                <label>Кратность работы {s.code}
                  <input type="number" min="1" max="20" step="1"
                    aria-label={'Кратность работы '+s.code} value={quantities[s.id]??'1'}
                    onChange={e=>setQuantities(prev=>({...prev,[s.id]:e.target.value}))}/>
                </label>
                <label>Коэффициент работы {s.code} (10000 = 1,00)
                  <input type="number" min="10000" max="30000" step="1"
                    aria-label={'Коэффициент работы '+s.code}
                    value={coefficients[s.id]??'10000'}
                    onChange={e=>setCoefficients(prev=>({...prev,[s.id]:e.target.value}))}/>
                </label>
                <label>Скидка работы {s.code} (100 = 1%)
                  <input type="number" min="0" max="2500" step="1"
                    aria-label={'Скидка работы '+s.code}
                    value={discounts[s.id]??'0'}
                    onChange={e=>setDiscounts(prev=>({...prev,[s.id]:e.target.value}))}/>
                </label>
              </>}
              <p>Расчёт {s.code}: {Number.isSafeInteger(computedAmount(s))?
                computedAmount(s)+' коп.':'укажите целую ставку'}</p>
            </>}
          </div>)}
          <button type="submit" disabled={!canPublish}>Опубликовать версию</button>
        </form>}
      {role==='manager'&&<p>Перед публикацией:
        {' '}<Link to={'/staff/auto-diagnostics?'+new URLSearchParams({
          org:org.trim(),loc:loc.trim(),order:order.trim()}).toString()}>
          Проверить диагностическое заключение</Link>.
      </p>}
      <button type="button" disabled={busy}
        onClick={()=>setRevision(n=>n+1)}>Обновить версии</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&ready&&<div role="group" aria-label="Повтор неизвестной операции">
      <button type="button" disabled={busy} onClick={()=>void send(pending.path,pending.body)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{setPending(null);setRevision(n=>n+1);}}>
        Перечитать версии</button>
    </div>}
    {status==='error'&&<button type="button" onClick={()=>setRevision(n=>n+1)}>
      Повторить загрузку</button>}
  </section>;
}
