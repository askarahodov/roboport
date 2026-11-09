import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Service={id:string;code:string;title:string;durationMinutes:number};
type Material={skuId:string;itemKind:'PART'|'CONSUMABLE';quantity:string;
  unitPriceMinor:string};
type Component={componentId:string;skuId:string;article:string;
  itemKind:'PART'|'CONSUMABLE';quantity:number;unitPriceMinor:number;amountMinor:number};
type Complex={id:string;code:string;title:string;services:Service[];materials:Component[]};
type Catalog={items:Complex[]};
type Selection={selectionId:string;complex:Complex;
  materialReceipts:{componentId:string;stockEventId:string}[]};
type Props={org:string;loc:string;order:string;onChanged:()=>void};
type Pending={path:string;body:string};

export function AutoWorkComplexes({org,loc,order,onChanged}:Props){
  const [catalog,setCatalog]=React.useState<Catalog|null>(null);
  const [selected,setSelected]=React.useState<Selection[]>([]);
  const [services,setServices]=React.useState<Service[]>([]);
  const [code,setCode]=React.useState('');
  const [title,setTitle]=React.useState('');
  const [chosen,setChosen]=React.useState<string[]>([]);
  const [materials,setMaterials]=React.useState<Material[]>([]);
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [reload,setReload]=React.useState(0);
  const branch='/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc);
  const catalogPath=branch+'/work-complexes';
  const selectedPath=branch+'/orders/'+encodeURIComponent(order)+'/complexes';

  React.useEffect(()=>{
    const controller=new AbortController();
    setCatalog(null);setServices([]);setSelected([]);setPending(null);setError('');
    void Promise.all([catalogPath,selectedPath,branch+'/service-catalog']
      .map(p=>fetch(p,{credentials:'include',cache:'no-store',signal:controller.signal})))
      .then(async responses=>{
        if(responses.some(r=>!r.ok))throw Error('access');
        const raw=await Promise.all(responses.map(r=>r.json())) as unknown[];
        const complexes=raw[0] as {items?:Complex[]};
        const selections=raw[1] as {items?:Selection[]};
        const svc=raw[2] as {items?:Service[]};
        if(!complexes||!Array.isArray(complexes.items)||complexes.items.length>50||
          !complexes.items.every(c=>uuid.test(c.id)&&typeof c.code==='string'&&
            Array.isArray(c.services)&&Array.isArray(c.materials))||
          !selections||!Array.isArray(selections.items)||selections.items.length>10||
          !svc||!Array.isArray(svc.items)||svc.items.length>100||
          !svc.items.every(s=>uuid.test(s.id)&&typeof s.title==='string'))
          throw Error('invalid');
        if(!controller.signal.aborted){
          setCatalog({items:complexes.items});setSelected(selections.items);setServices(svc.items);
        }
      }).catch(()=>{if(!controller.signal.aborted){
        setError('Не удалось прочитать комплексы, складские позиции или заказ-наряд.');
      }});
    return ()=>controller.abort();
  },[org,loc,order,reload]);

  async function send(path:string,body:string){
    if(busy)return;
    setBusy(true);setError('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        cache:'no-store',credentials:'include'});
      if(!csrf.ok)throw Error('csrf');
      const p=await csrf.json() as {csrfToken?:string};
      if(typeof p.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(path,{method:'POST',credentials:'include',
        cache:'no-store',headers:{'Content-Type':'application/json',
          'X-CSRF-Token':p.csrfToken},body});
      if(!response.ok){
        if([403,409,422].includes(response.status)){
          setPending(null);
          setError(response.status===403?'Нет полномочий или заказ недоступен.':
            response.status===409?'Недостаточно складского остатка, уже выбранная работа или конфликт версии.':
            'Неверный состав, количество или цена комплекса.');
          return;
        }
        throw Error('unknown');
      }
      setPending(null);setReload(x=>x+1);onChanged();
    }catch{
      setPending({path,body});
      setError('Результат неизвестен. Повторите прежний ключ или перечитайте выбор.');
    }finally{setBusy(false);}
  }

  const validMaterials=materials.length<=50&&materials.every(m=>
    uuid.test(m.skuId)&&/^[1-9][0-9]*$/.test(m.quantity)&&
    Number(m.quantity)<=1000000&&/^(0|[1-9][0-9]*)$/.test(m.unitPriceMinor)&&
    Number(m.unitPriceMinor)<=1e9&&
    (m.itemKind==='PART'||m.unitPriceMinor==='0')&&
    Number.isSafeInteger(Number(m.quantity))&&
    Number.isSafeInteger(Number(m.unitPriceMinor)))&&
    new Set(materials.map(m=>m.skuId)).size===materials.length&&
    materials.reduce((sum,m)=>sum+(Number(m.quantity)||0)*(Number(m.unitPriceMinor)||0),0)<=1e12;
  const canCreate=!busy&&!pending&&!!catalog&&/^[A-Z0-9_.-]{3,64}$/.test(code)&&
    title.length>=1&&title.length<=120&&chosen.length>0&&chosen.length<=50&&
    validMaterials;

  return <section aria-label="Комплексы работ и резерв материалов"
    style={{border:'1px solid #777',borderRadius:8,padding:'1rem',marginBlock:'1.5rem'}}>
    <h2>Комплексы работ и материалы</h2>
    <p>Менеджер собирает комплекс из услуг и складских SKU. Выбор комплекса
      привязывает услуги к ЗН и атомарно резервирует детали и расходники.
      Детали продаются отдельно, расходники имеют нулевую продажную цену;
      оба вида требуют реального складского остатка.</p>
    {!catalog&&!error&&<p role="status">Загрузка комплексов…</p>}
    {catalog&&<>
      <h3>Каталог филиала</h3>
      {catalog.items.length===0?<p>Активированных комплексов пока нет.</p>:
        <ul>{catalog.items.map(c=><li key={c.id}>
          <strong>{c.code} — {c.title}</strong>
          <p>Услуги: {c.services.map(s=>s.code).join(', ')}.
            {' '}Материалы: {c.materials.map(m=>
              m.article+' ('+m.itemKind+', '+m.quantity+' шт., '+m.amountMinor+' коп.)'
            ).join('; ')||'отсутствуют'}.</p>
          <button type="button" disabled={busy||!!pending||selected.some(s=>s.complex.id===c.id)}
            onClick={()=>void send(selectedPath,JSON.stringify({
              complexId:c.id,requestKey:crypto.randomUUID(),
            }))}>Выбрать комплекс {c.code} и зарезервировать</button>
        </li>)}</ul>}
      <h3>Выбранные комплексы ЗН</h3>
      {selected.length===0?<p>Комплексов в этом ЗН нет.</p>:
        <ul>{selected.map(item=><li key={item.selectionId}>
          {item.complex.code}: {item.materialReceipts.length} подтверждённых
          {' '}складских резервов, цена деталей
          {' '}{item.complex.materials.reduce((sum,m)=>sum+m.amountMinor,0)} коп.
        </li>)}</ul>}
      <form onSubmit={e=>{
        e.preventDefault();if(!canCreate)return;
        void send(catalogPath,JSON.stringify({requestKey:crypto.randomUUID(),
          code,title,serviceIds:chosen,
          materials:materials.map(m=>({skuId:m.skuId,itemKind:m.itemKind,
            quantity:Number(m.quantity),unitPriceMinor:Number(m.unitPriceMinor)})),
        }));
      }}>
        <h3>Создать неизменяемый комплекс</h3>
        <label>Код комплекса
          <input aria-label="Код комплекса" value={code} maxLength={64}
            onChange={e=>setCode(e.target.value.toUpperCase())}/>
        </label>
        <label>Название комплекса
          <input aria-label="Название комплекса" value={title} maxLength={120}
            onChange={e=>setTitle(e.target.value)}/>
        </label>
        <fieldset>
          <legend>Услуги комплекса</legend>
          {services.map(s=><label key={s.id} style={{display:'block'}}>
            <input type="checkbox" aria-label={'Услуга комплекса '+s.code}
              checked={chosen.includes(s.id)} onChange={e=>setChosen(old=>
                e.target.checked?[...old,s.id]:old.filter(id=>id!==s.id))}/>
            {s.code} — {s.title}, {s.durationMinutes} мин.
          </label>)}
        </fieldset>
        <h4>Материальные компоненты</h4>
        {materials.map((m,i)=><fieldset key={i}>
          <legend>Материал {i+1}</legend>
          <label>UUID складской позиции {i+1}
            <input aria-label={'SKU материала '+(i+1)} value={m.skuId}
              onChange={e=>setMaterials(old=>old.map((v,j)=>
                j===i?{...v,skuId:e.target.value}:v))}/></label>
          <label>Тип материала {i+1}
            <select aria-label={'Тип материала '+(i+1)} value={m.itemKind}
              onChange={e=>setMaterials(old=>old.map((v,j)=>
                j===i?{...v,itemKind:e.target.value as Material['itemKind'],
                    unitPriceMinor:e.target.value==='CONSUMABLE'?'0':v.unitPriceMinor}:v))}>
              <option value="PART">Деталь — продажа</option>
              <option value="CONSUMABLE">Расходник — без продажи</option>
            </select></label>
          <label>Количество материала {i+1}
            <input aria-label={'Количество материала '+(i+1)} type="number"
              min="1" max="1000000" step="1" value={m.quantity}
              onChange={e=>setMaterials(old=>old.map((v,j)=>
                j===i?{...v,quantity:e.target.value}:v))}/></label>
          <label>Цена материала {i+1}, коп./шт.
            <input aria-label={'Цена материала '+(i+1)} type="number"
              min="0" max="1000000000" step="1" value={m.unitPriceMinor}
              disabled={m.itemKind==='CONSUMABLE'} onChange={e=>
                setMaterials(old=>old.map((v,j)=>
                  j===i?{...v,unitPriceMinor:e.target.value}:v))}/></label>
          <button type="button" onClick={()=>setMaterials(old=>old.filter((_,j)=>j!==i))}>
            Убрать материал {i+1}</button>
        </fieldset>)}
        <button type="button" disabled={materials.length>=50}
          onClick={()=>setMaterials(old=>[...old,{
            skuId:'',itemKind:'PART',quantity:'1',unitPriceMinor:'0',
          }])}>Добавить материал в комплекс</button>
        <button type="submit" disabled={!canCreate}>Создать комплекс</button>
      </form>
    </>}
    {error&&<p role="alert">{error}</p>}
    {pending&&<div><button type="button" disabled={busy}
      onClick={()=>void send(pending.path,pending.body)}>Повторить тот же запрос</button>
      <button type="button" disabled={busy}
        onClick={()=>{setPending(null);setReload(x=>x+1);}}>Перечитать комплексы</button>
    </div>}
    <button type="button" disabled={busy}
      onClick={()=>setReload(x=>x+1)}>Обновить комплексы</button>
  </section>;
}
