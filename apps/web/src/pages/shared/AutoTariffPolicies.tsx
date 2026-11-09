import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Qualification='GENERAL'|'DIAGNOSTIC'|'MASTER';
type Policy={id:string;qualificationCode:Qualification;version:number;
  rateMinorPerHour:number;maxDiscountBps:number;proposedBy:string;
  approvedBy:string|null;createdAt:string;approvedAt:string|null};
type Policies={items:Policy[];active:Record<string,Policy>;syntheticOnly:true};
type Pending={path:string;body:string};
type Props={org:string;loc:string};

export function AutoTariffPolicies({org,loc}:Props){
  const [data,setData]=React.useState<Policies|null>(null);
  const [category,setCategory]=React.useState<Qualification>('GENERAL');
  const [rate,setRate]=React.useState('');
  const [discount,setDiscount]=React.useState('0');
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [revision,setRevision]=React.useState(0);
  const path='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/tariff-policies';
  React.useEffect(()=>{
    const controller=new AbortController();
    setData(null);setError('');setPending(null);
    void fetch(path,{credentials:'include',cache:'no-store',signal:controller.signal})
      .then(async result=>{
        if(!result.ok)throw Error('access');
        const raw=await result.json() as Policies;
        if(raw?.syntheticOnly!==true||!Array.isArray(raw.items)||raw.items.length>90||
          !raw.items.every(p=>uuid.test(p.id)&&
            ['GENERAL','DIAGNOSTIC','MASTER'].includes(p.qualificationCode)&&
            Number.isSafeInteger(p.rateMinorPerHour)&&
            Number.isInteger(p.maxDiscountBps)&&
            Number.isInteger(p.version))||!raw.active||typeof raw.active!=='object')
          throw Error('invalid policy');
        if(!controller.signal.aborted)setData(raw);
      }).catch(()=>{if(!controller.signal.aborted)setError('Не удалось прочитать тарифы. Проверьте полномочия и область филиала.');});
    return ()=>controller.abort();
  },[org,loc,revision]);

  async function send(destination:string,body:string){
    if(busy)return;
    setBusy(true);setError('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('csrf');
      const token=await csrf.json() as {csrfToken?:string};
      if(typeof token.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(destination,{method:'POST',credentials:'include',
        cache:'no-store',headers:{'Content-Type':'application/json',
        'X-CSRF-Token':token.csrfToken},body});
      if(!response.ok){
        if([403,409,422].includes(response.status)){
          setPending(null);setError(response.status===403?
            'Нет прав. Утверждать тариф должен другой менеджер филиала.':
            response.status===409?'Версия или ключ запроса конфликтуют. Перечитайте тарифы.':
            'Недопустимая категория, ставка или лимит скидки.');
          return;
        }
        throw Error('unknown');
      }
      setPending(null);setRevision(n=>n+1);
    }catch{
      setPending({path:destination,body});
      setError('Результат запроса неизвестен. Повторите его с тем же ключом или обновите список.');
    }finally{setBusy(false);}
  }
  const history=data?.items.filter(p=>p.qualificationCode===category)??[];
  const pendingCategory=history.some(p=>p.approvedBy===null);
  const canPublish=!!data&&!pending&&!busy&&!pendingCategory&&history.length<30&&
    /^(0|[1-9][0-9]*)$/.test(rate)&&Number.isSafeInteger(Number(rate))&&
    Number(rate)<=1e10&&/^(0|[1-9][0-9]*)$/.test(discount)&&
    Number.isSafeInteger(Number(discount))&&Number(discount)<=2500;
  return <section aria-label="Утверждённые тарифы филиала"
    style={{border:'1px solid #777',padding:'1rem',borderRadius:8,marginBlock:'1.5rem'}}>
    <h2>Тарифные правила филиала</h2>
    <p>Предложение и утверждение выполняют два разных менеджера.
      После утверждения новые сметы используют только согласованную
      ставку и скидку не выше лимита. Старые версии не редактируются.</p>
    {!data&&!error&&<p role="status">Загрузка тарифной политики…</p>}
    {data&&<>
      <h3>Утверждённые ставки</h3>
      {Object.keys(data.active).length===0?<p>Утверждённых тарифов пока нет.</p>:
        <ul>{Object.values(data.active).map(p=><li key={p.id}>
          {p.qualificationCode}: версия {p.version},
          {' '}{p.rateMinorPerHour} коп./час, скидка не выше
          {' '}{p.maxDiscountBps}/100 %.
        </li>)}</ul>}
      <h3>История предложений</h3>
      <ul>{data.items.map(p=><li key={p.id}>
        {p.qualificationCode} · версия {p.version} · ставка
        {' '}{p.rateMinorPerHour} коп./час · предел скидки {p.maxDiscountBps}/100 %
        {' '}— {p.approvedBy?'УТВЕРЖДЕНО':'ОЖИДАЕТ УТВЕРЖДЕНИЯ'}.
        {!p.approvedBy&&!pending&&<button type="button" disabled={busy}
          onClick={()=>void send(path+'/'+p.id+'/approve',JSON.stringify({
            requestKey:crypto.randomUUID(),
          }))}>Утвердить тариф {p.qualificationCode}, версия {p.version}
          </button>}
      </li>)}</ul>
      <form onSubmit={e=>{
        e.preventDefault();if(!canPublish)return;
        void send(path,JSON.stringify({requestKey:crypto.randomUUID(),
          qualificationCode:category,expectedVersion:history.length,
          rateMinorPerHour:Number(rate),maxDiscountBps:Number(discount)}));
      }}>
        <h3>Предложить новый тариф</h3>
        <label>Тарифная категория филиала
          <select aria-label="Тарифная категория филиала" value={category}
            onChange={e=>setCategory(e.target.value as Qualification)}>
            <option value="GENERAL">Общий</option>
            <option value="DIAGNOSTIC">Диагностика</option>
            <option value="MASTER">Мастер</option>
          </select>
        </label>
        <label>Новая ставка, копеек за нормо-час
          <input type="number" min="0" max="10000000000" step="1"
            aria-label="Новая ставка тарифа"
            value={rate} onChange={e=>setRate(e.target.value)}/>
        </label>
        <label>Максимальная скидка, базисные пункты (100 = 1%)
          <input type="number" min="0" max="2500" step="1"
            aria-label="Максимальная скидка тарифа"
            value={discount} onChange={e=>setDiscount(e.target.value)}/>
        </label>
        {pendingCategory&&<p>У этой категории есть неподтверждённая версия.
          Прежде чем создавать следующую, второй менеджер должен утвердить её.</p>}
        <button type="submit" disabled={!canPublish}>Предложить тариф</button>
      </form>
    </>}
    {error&&<p role="alert">{error}</p>}
    {pending&&<div><button type="button" disabled={busy}
      onClick={()=>void send(pending.path,pending.body)}>Повторить прежний запрос</button>
      <button type="button" disabled={busy}
        onClick={()=>{setPending(null);setRevision(n=>n+1);}}>Перечитать тарифы</button>
    </div>}
    <button type="button" disabled={busy} onClick={()=>setRevision(n=>n+1)}>
      Обновить тарифы филиала</button>
  </section>;
}
