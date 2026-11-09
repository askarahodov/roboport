import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Mode='GENERAL'|'DIAGNOSTIC'|'MASTER';
type Service={id:string;code:string;title:string;durationMinutes:number};
type Line={serviceId:string;code:string;title:string;durationMinutes:number;amountMinor:number;
  qualificationCode:Mode;rateMinorPerHour:number;quantity:number;
  coefficientBps:number;discountBps:number};
type Extra={id:string;version:number;baseRevisionId:string;totalMinor:number;
  digest:string;lines:Line[];decision:'APPROVED'|'DECLINED'|null};
type ExtraState={items:Extra[];latestVersion:number;syntheticOnly:true;releaseAllowed:false};
type Pending={path:string;body:string};
type Params={role:'manager'|'client';baseRevisionId:string;baseServiceIds:string[];
  org:string;loc:string;order:string};

function readExtras(raw:unknown):ExtraState{
  if(!raw||typeof raw!=='object')throw Error('extra');
  const p=raw as Record<string,unknown>;
  if(p.syntheticOnly!==true||p.releaseAllowed!==false||
     !Array.isArray(p.items)||p.items.length>10||
     !Number.isInteger(p.latestVersion)||!p.items.every((v:unknown)=>{
       if(!v||typeof v!=='object')return false;
       const row=v as Record<string,unknown>;
       return typeof row.id==='string'&&uuid.test(row.id)&&
         typeof row.baseRevisionId==='string'&&uuid.test(row.baseRevisionId)&&
         typeof row.digest==='string'&&row.digest.length===64&&
         typeof row.totalMinor==='number'&&Number.isSafeInteger(row.totalMinor)&&
         ['APPROVED','DECLINED',null].includes(row.decision as null)&&
         Array.isArray(row.lines)&&row.lines.length<=20&&row.lines.every((x:unknown)=>
           x!==null&&typeof x==='object'&&'code' in x&&
           typeof x.code==='string'&&'title' in x&&typeof x.title==='string'&&
           'amountMinor' in x&&typeof x.amountMinor==='number');
     }))throw Error('extra');
  return p as ExtraState;
}

export function AutoAdditionalWork({role,baseRevisionId,baseServiceIds,org,loc,order}:Params){
  const [items,setItems]=React.useState<ExtraState|null>(null);
  const [catalog,setCatalog]=React.useState<Service[]>([]);
  const [selected,setSelected]=React.useState<string[]>([]);
  const [rates,setRates]=React.useState<Record<string,string>>({});
  const [qty,setQty]=React.useState<Record<string,string>>({});
  const [coeff,setCoeff]=React.useState<Record<string,string>>({});
  const [discount,setDiscount]=React.useState<Record<string,string>>({});
  const [qualification,setQualification]=React.useState<Record<string,Mode>>({});
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [reload,setReload]=React.useState(0);
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc);
  const endpoint=base+'/orders/'+encodeURIComponent(order)+'/extras';
  const scope=[org,loc,order,baseRevisionId,role].join(':');

  React.useEffect(()=>{
    const controller=new AbortController();
    setItems(null);setError('');setPending(null);
    const paths=[endpoint];
    if(role==='manager')paths.push(base+'/service-catalog');
    void Promise.all(paths.map(path=>fetch(path,{credentials:'include',cache:'no-store',
      signal:controller.signal}))).then(async responses=>{
      if(responses.some(r=>!r.ok))throw Error('scope');
      const data=await Promise.all(responses.map(r=>r.json())) as unknown[];
      const extra=readExtras(data[0]);
      let services:Service[]=[];
      if(role==='manager'){
        const payload=data[1] as {items?:Service[]};
        if(!payload||!Array.isArray(payload.items)||payload.items.length>100||
          !payload.items.every(s=>uuid.test(s.id)&&typeof s.code==='string'&&
            typeof s.title==='string'&&Number.isInteger(s.durationMinutes)))
          throw Error('catalog');
        services=payload.items.filter(s=>!baseServiceIds.includes(s.id));
      }
      if(!controller.signal.aborted){setItems(extra);setCatalog(services);}
    }).catch(()=>{if(!controller.signal.aborted)setError('Не удалось получить данные дополнительных работ.');});
    return ()=>controller.abort();
  },[scope,reload]);

  async function send(path:string,body:string){
    if(busy)return;
    setBusy(true);setError('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',cache:'no-store',
        credentials:'include'});
      if(!csrf.ok)throw Error('csrf');
      const payload=await csrf.json() as {csrfToken?:string};
      if(typeof payload.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(path,{method:'POST',credentials:'include',
        cache:'no-store',headers:{'Content-Type':'application/json',
          'X-CSRF-Token':payload.csrfToken},body});
      if(!response.ok){
        if([403,409,422].includes(response.status)){
          setError(response.status===403?'Доступ к дополнительным работам запрещён.':
            response.status===409?'Конфликт версии или согласия. Перечитайте документы.':
            'Неверная кратность, ставка или скидка.');
          setPending(null);setReload(n=>n+1);return;
        }
        throw Error('unknown');
      }
      setPending(null);setReload(n=>n+1);
    }catch{
      setPending({path,body});
      setError('Ответ неизвестен. Повторите тот же запрос и ключ либо перечитайте версии.');
    }finally{setBusy(false);}
  }

  function amount(service:Service){
    const raw=[rates[service.id]??'',qty[service.id]??'1',
      coeff[service.id]??'10000',discount[service.id]??'0'];
    if(raw.some(v=>!/^(0|[1-9][0-9]*)$/.test(v)))return NaN;
    const values=raw.map(Number),[rate,quantity,coefficient,discountBps]=values;
    if(!values.every(Number.isSafeInteger)||rate>1e10||quantity<1||quantity>20||
      coefficient<10000||coefficient>30000||discountBps<0||discountBps>2500)
      return NaN;
    const minor=(BigInt(service.durationMinutes)*BigInt(rate)*BigInt(quantity)*
      BigInt(coefficient)*BigInt(10000-discountBps)+3000000000n)/6000000000n;
    return minor<=1000000000000n?Number(minor):NaN;
  }
  const newest=items?.items[items.items.length-1];
  const approved=items?.items.some(row=>row.decision==='APPROVED');
  const selectedServices=catalog.filter(s=>selected.includes(s.id));
  const canPublish=role==='manager'&&!busy&&!pending&&!!items&&!approved&&
    (items.latestVersion<10)&&selectedServices.length>0&&selectedServices.length<=20&&
    selectedServices.every(s=>Number.isSafeInteger(amount(s)))&&
    selectedServices.reduce((sum,s)=>sum+amount(s),0)<=1e12;

  return <section aria-label="Дополнительные работы и согласие клиента"
    style={{border:'1px solid #777',borderRadius:8,padding:'1rem',marginBlock:'1.5rem'}}>
    <h2>Дополнительные работы: отдельное согласование</h2>
    <p>Исходная смета остаётся неизменной. Новые работы можно выполнять только
      после отдельного решения клиента по точной версии. Синтетический контур.</p>
    {!items&&!error&&<p role="status">Загрузка дополнительных работ…</p>}
    {items&&<ol>{items.items.map(v=><li key={v.id}>
      <h3>Допработы, версия {v.version}: {v.totalMinor} коп.</h3>
      <p>Решение клиента: {v.decision??(v===newest?'ОЖИДАЕТ РЕШЕНИЯ':'ЗАМЕНЕНА')}</p>
      <p>SHA-256: <code>{v.digest}</code></p>
      <ul>{v.lines.map(line=><li key={line.serviceId}>
        {line.code} — {line.title}: {line.amountMinor} коп.
        <p>{line.durationMinutes} мин. × {line.rateMinorPerHour} коп./час,
          {' '}категория {line.qualificationCode}, кратность {line.quantity},
          {' '}коэффициент {line.coefficientBps}/10000, скидка {line.discountBps}/100%.</p>
      </li>)}</ul>
      {role==='client'&&v===newest&&!v.decision&&!pending&&
        <div><button type="button" disabled={busy} onClick={()=>
          void send(endpoint+'/decision',JSON.stringify({
            revisionId:v.id,requestKey:crypto.randomUUID(),decision:'APPROVED',
          }))}>Согласовать дополнительные работы, версия {v.version}</button>
        <button type="button" disabled={busy} onClick={()=>
          void send(endpoint+'/decision',JSON.stringify({
            revisionId:v.id,requestKey:crypto.randomUUID(),decision:'DECLINED',
          }))}>Отклонить дополнительные работы, версия {v.version}</button></div>}
    </li>)}</ol>}
    {items&&role==='manager'&&!approved&&items.latestVersion<10&&
      <form onSubmit={e=>{
        e.preventDefault();
        if(!canPublish)return;
        void send(endpoint,JSON.stringify({baseRevisionId,
          requestKey:crypto.randomUUID(),expectedVersion:items.latestVersion,
          workRates:selectedServices.map(s=>({
            serviceId:s.id,qualificationCode:qualification[s.id]??'GENERAL',
            rateMinorPerHour:Number(rates[s.id]),
            quantity:Number(qty[s.id]??'1'),
            coefficientBps:Number(coeff[s.id]??'10000'),
            discountBps:Number(discount[s.id]??'0'),
          }))}));
      }}>
        <h3>Новая версия допработ</h3>
        {catalog.map(s=><fieldset key={s.id} style={{marginBlock:'.5rem'}}>
          <legend>{s.code} — {s.title}, {s.durationMinutes} мин.</legend>
          <label><input type="checkbox" aria-label={'Добавить допработу '+s.code}
            checked={selected.includes(s.id)} onChange={e=>setSelected(old=>
              e.target.checked?[...old,s.id]:old.filter(id=>id!==s.id))}/>
            Включить в дополнительные работы</label>
          {selected.includes(s.id)&&<div>
            <label>Категория допработы {s.code}
              <select aria-label={'Категория допработы '+s.code}
                value={qualification[s.id]??'GENERAL'} onChange={e=>
                  setQualification(old=>({...old,[s.id]:e.target.value as Mode}))}>
                <option value="GENERAL">Общий</option>
                <option value="DIAGNOSTIC">Диагностика</option>
                <option value="MASTER">Мастер</option>
              </select></label>
            {([
              ['Ставка допработы',rates,setRates,'','10000000000'],
              ['Кратность допработы',qty,setQty,'1','20'],
              ['Коэффициент допработы',coeff,setCoeff,'10000','30000'],
              ['Скидка допработы',discount,setDiscount,'0','2500'],
            ] as const).map(([name,values,setter,fallback,max])=>
              <label key={name}>{name} {s.code}
                <input type="number" min="0" max={max} step="1"
                  aria-label={name+' '+s.code} value={values[s.id]??fallback}
                  onChange={e=>setter(old=>({...old,[s.id]:e.target.value}))}/>
              </label>)}
            <p>Расчёт допработы {s.code}: {Number.isSafeInteger(amount(s))?
              amount(s)+' коп.':'проверьте ставку и множители'}</p>
          </div>}
        </fieldset>)}
        {catalog.length===0&&<p>Нет доступных новых услуг в филиальном каталоге.</p>}
        <button type="submit" disabled={!canPublish}>Опубликовать версию допработ</button>
      </form>}
    {error&&<p role="alert">{error}</p>}
    {pending&&<div><button type="button" disabled={busy}
      onClick={()=>void send(pending.path,pending.body)}>Повторить ту же операцию</button>
      <button type="button" disabled={busy}
        onClick={()=>{setPending(null);setReload(n=>n+1);}}>Перечитать допработы</button></div>}
    <button type="button" disabled={busy} onClick={()=>setReload(n=>n+1)}>
      Обновить дополнительные работы</button>
  </section>;
}
