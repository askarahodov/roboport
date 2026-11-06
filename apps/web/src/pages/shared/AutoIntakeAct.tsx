import React from 'react';
import { Link,useSearchParams } from 'react-router-dom';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sha=/^[0-9a-f]{64}$/;
const areas=['FRONT','REAR','LEFT','RIGHT','ROOF','CABIN'] as const;
const damageCodes=['SCRATCH','DENT','CRACK','MISSING','CORROSION'] as const;
const severities=['MINOR','MODERATE','SEVERE'] as const;
const inventoryCodes=['KEYS','SPARE_WHEEL','TOOLS','DOCS','CHARGER'] as const;
const inventoryStates=['PRESENT','MISSING','DAMAGED','NOT_CHECKED'] as const;
const kinds=['PASSENGER','SUV','VAN','TRUCK','MOTORCYCLE'] as const;
type Area=typeof areas[number];
type Damage={areaCode:Area;damageCode:typeof damageCodes[number];
  severity:typeof severities[number]};
type Inventory={itemCode:typeof inventoryCodes[number];
  condition:typeof inventoryStates[number]};
type Photo={areaCode:Area;imageBase64:string};
type PhotoMeta={areaCode:Area;sha256:string;mime:'image/png';
  width:number;height:number;index:number;path:string};
type Act={id:string;version:number;vehicleKind:typeof kinds[number];damages:Damage[];
  inventory:Inventory[];photos:PhotoMeta[];sha256:string;createdAt:string;actorId:string};
type State={syntheticOnly:true;releaseAllowed:false;legalSignatureStatus:'NOT_IMPLEMENTED';
  inspection:null|{id:string;odometerKm:number;fuelPercent:number;exteriorCode:string};
  locked:boolean;latestVersion:number;items:Act[]};
type Pending={body:string;scope:string};
type Status='idle'|'loading'|'ready'|'error'|'denied';

const areaLabels:Record<Area,string>={
  FRONT:'Перед',REAR:'Задняя часть',LEFT:'Левая сторона',RIGHT:'Правая сторона',
  ROOF:'Крыша',CABIN:'Салон',
};
const damageLabels:Record<Damage['damageCode'],string>={
  SCRATCH:'Царапина',DENT:'Вмятина',CRACK:'Трещина',
  MISSING:'Отсутствующая деталь',CORROSION:'Коррозия',
};
const inventoryLabels:Record<Inventory['itemCode'],string>={
  KEYS:'Ключи',SPARE_WHEEL:'Запасное колесо',TOOLS:'Инструменты',
  DOCS:'Документы',CHARGER:'Зарядное устройство',
};
const inventoryStateLabels:Record<Inventory['condition'],string>={
  PRESENT:'Есть',MISSING:'Нет',DAMAGED:'Повреждено',NOT_CHECKED:'Не проверено',
};
const initialInventory=():Inventory[]=>
  inventoryCodes.map(code=>({itemCode:code,condition:'NOT_CHECKED'}));

function validState(raw:unknown):State{
  if(!raw||typeof raw!=='object')throw Error('bad act state');
  const v=raw as State;
  if(v.syntheticOnly!==true||v.releaseAllowed!==false||
    v.legalSignatureStatus!=='NOT_IMPLEMENTED'||
    typeof v.locked!=='boolean'||!Array.isArray(v.items)||v.items.length>20||
    !Number.isInteger(v.latestVersion)||v.latestVersion!==v.items.length||
    !(v.inspection===null||(typeof v.inspection==='object'&&
      uuid.test(v.inspection.id)&&Number.isInteger(v.inspection.odometerKm)&&
      Number.isInteger(v.inspection.fuelPercent)))||
    !v.items.every(item=>item&&uuid.test(item.id)&&uuid.test(item.actorId)&&
      Number.isInteger(item.version)&&item.version>=1&&item.version<=20&&
      kinds.includes(item.vehicleKind)&&sha.test(item.sha256)&&
      typeof item.createdAt==='string'&&Number.isFinite(Date.parse(item.createdAt))&&
      Array.isArray(item.damages)&&item.damages.length<=12&&
      item.damages.every(d=>d&&areas.includes(d.areaCode)&&
        damageCodes.includes(d.damageCode)&&severities.includes(d.severity))&&
      Array.isArray(item.inventory)&&item.inventory.length===5&&
      item.inventory.every(i=>i&&inventoryCodes.includes(i.itemCode)&&
        inventoryStates.includes(i.condition))&&
      new Set(item.inventory.map(i=>i.itemCode)).size===5&&
      Array.isArray(item.photos)&&item.photos.length<=3&&
      item.photos.every(p=>p&&areas.includes(p.areaCode)&&p.mime==='image/png'&&
        sha.test(p.sha256)&&Number.isInteger(p.width)&&Number.isInteger(p.height)&&
        Number.isInteger(p.index)&&p.index>=1&&p.index<=3)))throw Error('bad act state');
  return v;
}

async function pixelOnlyPng(file:File):Promise<string>{
  if(file.size===0||file.size>5*1024*1024||
     !['image/jpeg','image/png'].includes(file.type))throw Error('unsupported photo');
  const image=await createImageBitmap(file);
  try{
    for(const limit of [256,192,128]){
      const factor=Math.min(1,limit/Math.max(image.width,image.height));
      const width=Math.max(1,Math.round(image.width*factor));
      const height=Math.max(1,Math.round(image.height*factor));
      const canvas=document.createElement('canvas');
      canvas.width=width;canvas.height=height;
      const ctx=canvas.getContext('2d');
      if(!ctx)throw Error('canvas unavailable');
      ctx.drawImage(image,0,0,width,height);
      // Canvas strips EXIF/GPS/metadata; the API independently checks the PNG.
      const encoded=canvas.toDataURL('image/png').split(',')[1];
      if(encoded.length<=262144)return encoded;
    }
  }finally{image.close();}
  throw Error('photo too large');
}

export function AutoIntakeAct({role}:{role:'staff'|'client'}){
  const [query]=useSearchParams();
  const [org,setOrg]=React.useState(query.get('org')??'');
  const [loc,setLoc]=React.useState(query.get('loc')??'');
  const [order,setOrder]=React.useState(query.get('order')??'');
  const [revision,setRevision]=React.useState(0);
  const [status,setStatus]=React.useState<Status>('idle');
  const [loaded,setLoaded]=React.useState('');
  const [data,setData]=React.useState<State|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [message,setMessage]=React.useState('');
  const [kind,setKind]=React.useState<Act['vehicleKind']>('PASSENGER');
  const [inventory,setInventory]=React.useState<Inventory[]>(initialInventory);
  const [damages,setDamages]=React.useState<Damage[]>([]);
  const [area,setArea]=React.useState<Area>('FRONT');
  const [damageCode,setDamageCode]=React.useState<Damage['damageCode']>('SCRATCH');
  const [severity,setSeverity]=React.useState<Damage['severity']>('MINOR');
  const [photoArea,setPhotoArea]=React.useState<Area>('FRONT');
  const [photos,setPhotos]=React.useState<Photo[]>([]);
  const valid=[org,loc,order].every(x=>uuid.test(x.trim()));
  const scope=[org.trim(),loc.trim(),order.trim()].join(':');
  const root='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/orders/'+encodeURIComponent(order.trim());
  const path=root+'/intake-acts';
  const ready=status==='ready'&&loaded===scope&&data!==null;
  React.useEffect(()=>{
    setStatus(valid?'loading':'idle');setLoaded('');setData(null);
    setMessage('');setPending(null);setPhotos([]);setDamages([]);
    setInventory(initialInventory());
    if(!valid)return;
    const ctl=new AbortController();
    void fetch(path,{credentials:'include',cache:'no-store',signal:ctl.signal})
      .then(async response=>{
        if(ctl.signal.aborted)return;
        if([401,403,404].includes(response.status)){setStatus('denied');return;}
        if(!response.ok)throw Error('read failed');
        const parsed=validState(await response.json());
        if(!ctl.signal.aborted){setLoaded(scope);setData(parsed);setStatus('ready');}
      }).catch(()=>{if(!ctl.signal.aborted)setStatus('error');});
    return ()=>ctl.abort();
  },[scope,revision,valid]);

  async function submit(request:Pending){
    if(!ready||busy||role!=='staff'||request.scope!==scope)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',
        credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('csrf');
      const value:unknown=await csrf.json();
      if(!value||typeof value!=='object'||!('csrfToken' in value)||
        typeof value.csrfToken!=='string')throw Error('csrf');
      const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken},
        body:request.body});
      if([401,403,404].includes(response.status)){
        setData(null);setStatus('denied');setPending(null);return;
      }
      if(response.status===409||response.status===422){
        setPending(null);setMessage(response.status===409?
          'Конфликт версии, осмотра либо закрытия этапа. Обновите журнал.':
          'Проверьте повреждения, комплектность и допустимость фотографий.');
        setRevision(v=>v+1);return;
      }
      if(!response.ok)throw Error('unknown');
      setPending(null);setRevision(v=>v+1);
    }catch{
      setPending(request);
      setMessage('Результат неизвестен. Повторите тот же запрос с исходным ключом.');
    }finally{setBusy(false);}
  }
  const canWrite=role==='staff'&&ready&&!busy&&!pending&&
    data.inspection!==null&&!data.locked&&data.latestVersion<20&&
    (!damages.length||photos.length>0);
  const last=data?.items.at(-1);
  return <section aria-label="Версионированный акт приёмки автомобиля (dev)"
    style={{maxWidth:'75rem',margin:'2rem auto',overflowWrap:'anywhere'}}>
    <h1>{role==='staff'?'Оформление':'Просмотр'} акта приёмки автомобиля (dev)</h1>
    <p>Только синтетические данные. Документ хранит исторические версии, но
      не является юридически подписанным актом и не разрешает выдачу автомобиля.</p>
    <label>UUID организации <input aria-label="UUID организации"
      value={org} onChange={e=>setOrg(e.target.value)} disabled={busy}/></label>
    <label>UUID филиала <input aria-label="UUID филиала"
      value={loc} onChange={e=>setLoc(e.target.value)} disabled={busy}/></label>
    <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда"
      value={order} onChange={e=>setOrder(e.target.value)} disabled={busy}/></label>
    {status==='idle'&&<p>Введите идентификаторы своего заказ-наряда.</p>}
    {status==='loading'&&<p role="status">Загрузка версий акта…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён или связь с автомобилем отозвана.</p>}
    {status==='error'&&<><p role="alert">Не удалось получить акт.</p>
      <button type="button" onClick={()=>setRevision(v=>v+1)}>Повторить загрузку</button></>}
    {ready&&data&&<>
      <h2>Журнал актов — {data.latestVersion} верс.</h2>
      {data.inspection?<p>Исходный осмотр: {data.inspection.odometerKm} км,
        топливо {data.inspection.fuelPercent}%, кузов {data.inspection.exteriorCode}.
        Показания исходного осмотра не переписываются.</p>:
        <p role="status">Для первой версии требуется приёмочный осмотр (INTAKE).</p>}
      {data.locked&&<p role="status">Смета или QC уже зафиксированы.
        Новая версия акта недоступна; история остаётся доступной.</p>}
      {data.items.length===0?<p>Версий акта ещё нет.</p>:
        <ol>{data.items.map(item=><li key={item.id}>
          <section aria-label={'Акт версия '+item.version}>
            <h3>Акт №{item.version} · {item.vehicleKind}</h3>
            <p>Создан {new Date(item.createdAt).toLocaleString('ru-RU')}.
              SHA-256: <code>{item.sha256}</code></p>
            <h4>Повреждения</h4>
            {item.damages.length===0?<p>Повреждения не отмечены.</p>:
              <ul>{item.damages.map(d=><li key={d.areaCode+d.damageCode}>
                {areaLabels[d.areaCode]}: {damageLabels[d.damageCode]} · {d.severity}
              </li>)}</ul>}
            <h4>Комплектность</h4>
            <ul>{item.inventory.map(i=><li key={i.itemCode}>
              {inventoryLabels[i.itemCode]}: {inventoryStateLabels[i.condition]}
            </li>)}</ul>
            <h4>Фотографии ({item.photos.length})</h4>
            <div style={{display:'flex',flexWrap:'wrap',gap:12}}>
            {item.photos.map(p=><figure key={p.index} style={{margin:0,maxWidth:240}}>
              <img alt={'Фото акта '+item.version+': '+areaLabels[p.areaCode]}
                src={path+'/'+item.id+'/photos/'+p.index}
                loading="lazy" style={{maxWidth:'100%',borderRadius:4}}/>
              <figcaption>{areaLabels[p.areaCode]} · {p.width}×{p.height}.
                {' '}SHA-256: <code>{p.sha256.slice(0,12)}…</code></figcaption>
            </figure>)}
            </div>
          </section>
        </li>)}</ol>}
      {last&&role==='staff'&&<p>Для следующей версии можно зафиксировать изменения.
        Старый акт №{last.version} останется неизменным.</p>}
      <button type="button" onClick={()=>window.print()}>Печатать предпросмотр акта</button>
      <button type="button" disabled={busy}
        onClick={()=>setRevision(v=>v+1)}>Обновить журнал актов</button>
      {role==='staff'&&data.inspection&&!data.locked&&data.latestVersion<20&&
        <section aria-label="Новая версия акта">
          <h2>Новая неизменяемая версия (INTAKE)</h2>
          <label>Тип транспорта <select aria-label="Тип транспорта" value={kind}
            onChange={e=>setKind(e.target.value as Act['vehicleKind'])}>
            {kinds.map(v=><option value={v} key={v}>{v}</option>)}
          </select></label>
          <h3>Комплектность</h3>
          {inventory.map(i=><label key={i.itemCode}>{inventoryLabels[i.itemCode]}
            <select aria-label={'Комплектность '+inventoryLabels[i.itemCode]}
              value={i.condition} onChange={e=>setInventory(prev=>prev.map(entry=>
                entry.itemCode===i.itemCode?{...entry,
                  condition:e.target.value as Inventory['condition']}:entry))}>
              {inventoryStates.map(code=><option value={code} key={code}>
                {inventoryStateLabels[code]}</option>)}
            </select></label>)}
          <h3>Повреждения ({damages.length}/12)</h3>
          <label>Зона повреждения <select aria-label="Зона повреждения" value={area}
            onChange={e=>setArea(e.target.value as Area)}>
            {areas.map(code=><option value={code} key={code}>
              {areaLabels[code]}</option>)}
          </select></label>
          <label>Тип повреждения <select aria-label="Тип повреждения"
            value={damageCode} onChange={e=>setDamageCode(
              e.target.value as Damage['damageCode'])}>
            {damageCodes.map(code=><option value={code} key={code}>
              {damageLabels[code]}</option>)}
          </select></label>
          <label>Степень повреждения <select aria-label="Степень повреждения"
            value={severity} onChange={e=>setSeverity(
              e.target.value as Damage['severity'])}>
            {severities.map(code=><option value={code} key={code}>{code}</option>)}
          </select></label>
          <button type="button" disabled={busy||damages.length>=12||
            damages.some(x=>x.areaCode===area&&x.damageCode===damageCode)}
            onClick={()=>setDamages(prev=>[...prev,{areaCode:area,damageCode,severity}])}>
            Добавить повреждение</button>
          <ul>{damages.map((d,i)=><li key={d.areaCode+d.damageCode}>
            {areaLabels[d.areaCode]} — {damageLabels[d.damageCode]} ({d.severity})
            <button type="button" disabled={busy} onClick={()=>setDamages(prev=>
              prev.filter((_,idx)=>idx!==i))}>Удалить отметку</button>
          </li>)}</ul>
          <h3>Фотофиксация (до 3 снимков)</h3>
          <p>Исходные JPEG/PNG локально переводятся в PNG без метаданных.
            Файлы остаются приватными; для отмеченных повреждений нужен хотя бы один снимок.</p>
          <label>Зона фотоснимка <select aria-label="Зона фотоснимка"
            value={photoArea} onChange={e=>setPhotoArea(e.target.value as Area)}>
            {areas.map(code=><option value={code} key={code}>
              {areaLabels[code]}</option>)}
          </select></label>
          <label>Добавить фотографии <input aria-label="Добавить фотографии"
            type="file" accept="image/jpeg,image/png" multiple
            disabled={busy||photos.length>=3}
            onChange={e=>{
              const files=Array.from(e.target.files??[]);
              e.currentTarget.value='';
              if(files.length+photos.length>3){
                setMessage('Не более трёх фотографий в одной версии.');return;
              }
              void (async()=>{
                setBusy(true);setMessage('');
                try{
                  const encoded=await Promise.all(files.map(pixelOnlyPng));
                  setPhotos(prev=>[...prev,...encoded.map(imageBase64=>({
                    areaCode:photoArea,imageBase64,
                  }))]);
                }catch{
                  setMessage('Фото не поддерживается или слишком велико.');
                }finally{setBusy(false);}
              })();
            }}/></label>
          <ul>{photos.map((p,i)=><li key={i}>
            {areaLabels[p.areaCode]} — PNG без метаданных
            <button type="button" disabled={busy} onClick={()=>setPhotos(prev=>
              prev.filter((_,idx)=>idx!==i))}>Убрать фото</button>
          </li>)}</ul>
          <button type="button" disabled={!canWrite} onClick={()=>{
            if(!data)return;
            const body=JSON.stringify({expectedVersion:data.latestVersion,
              requestKey:crypto.randomUUID(),
              act:{vehicleKind:kind,damages,inventory,photos}});
            void submit({body,scope});
          }}>Зафиксировать версию акта</button>
        </section>}
      {role==='staff'&&<p>Приёмочный осмотр и QC:
        {' '}<Link to={'/staff/auto-workshop-qc?'+new URLSearchParams({
          org:org.trim(),loc:loc.trim(),order:order.trim()}).toString()}>
          открыть контроль качества</Link>.</p>}
    </>}
    {message&&<p role="alert">{message}</p>}
    {ready&&pending&&<div role="group" aria-label="Неизвестный результат акта">
      <button type="button" disabled={busy} onClick={()=>void submit(pending)}>
        Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{
        setPending(null);setRevision(v=>v+1);
      }}>Перечитать журнал</button>
    </div>}
  </section>;
}
