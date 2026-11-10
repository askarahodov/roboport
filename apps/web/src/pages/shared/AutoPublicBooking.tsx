import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const pub='/api/v1/dev/auto/public-booking';
const dayNow=()=>new Date().toISOString().slice(0,10);
const newKey=()=>crypto.randomUUID();
type Offer={id:string;organizationId:string;locationId:string;bayId:string;
  serviceId:string;policyVersion:string;serviceTitle:string;durationMinutes:number;
  bayCode:string;timeZone:string;materialsReady:boolean};
type Slot={startsAt:string;endsAt:string};
type Item={requestId:string;offerId:string;startsAt:string;endsAt:string;createdAt:string;
  status:'AWAITING_VERIFICATION'|'WAITING_FOR_STAFF'|'BOOKED';
  appointmentId:string|null;materials:{skuId:string;kind:'PART'|'CONSUMABLE';quantity:number}[]};
type Receipt={requestId:string;receiptKey:string};
type Pending={url:string;body:string;auth:boolean};
type Mode='public'|'client'|'staff';

function parseOffers(data:unknown):Offer[]{
  const x=data as {syntheticOnly?:boolean;items?:Offer[]};
  if(!x||x.syntheticOnly!==true||!Array.isArray(x.items)||x.items.length>50||
     !x.items.every(i=>i&&[i.id,i.organizationId,i.locationId,
        i.bayId,i.serviceId].every(v=>uuid.test(v))&&
        typeof i.serviceTitle==='string'&&i.serviceTitle.length<=180&&
        Number.isInteger(i.durationMinutes)&&i.durationMinutes>0&&
        typeof i.timeZone==='string'&&typeof i.materialsReady==='boolean'))
    throw new Error('Некорректный каталог публичных услуг');
  return x.items;
}
function parseQueue(data:unknown):Item[]{
  const x=data as {syntheticOnly?:boolean;items?:Item[]};
  if(!x||x.syntheticOnly!==true||!Array.isArray(x.items)||x.items.length>50||
     !x.items.every(i=>i&&uuid.test(i.requestId)&&uuid.test(i.offerId)&&
      ['AWAITING_VERIFICATION','WAITING_FOR_STAFF','BOOKED'].includes(i.status)&&
      (i.appointmentId===null||uuid.test(i.appointmentId))&&
      Array.isArray(i.materials)&&i.materials.length<=8&&
      i.materials.every(m=>uuid.test(m.skuId)&&
        ['PART','CONSUMABLE'].includes(m.kind)&&Number.isSafeInteger(m.quantity)&&m.quantity>0)))
    throw new Error('Некорректный журнал заявок');
  return x.items;
}
function parseSlots(data:unknown):Slot[]{
  const x=data as {syntheticOnly?:boolean;guaranteed?:boolean;items?:Slot[]};
  if(!x||x.syntheticOnly!==true||x.guaranteed!==false||
     !Array.isArray(x.items)||x.items.length>50||
     !x.items.every(i=>i&&Number.isFinite(Date.parse(i.startsAt))&&
       Number.isFinite(Date.parse(i.endsAt))))
    throw new Error('Недостоверный список времён');
  return x.items;
}
function localTime(value:string,zone:string){
  try {return new Intl.DateTimeFormat('ru-RU',{
    timeZone:zone,day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'
  }).format(new Date(value));}catch{return value;}
}
function codeMessage(c:string){
  const labels:Record<string,string>={
    FORBIDDEN:'Доступ запрещён: проверьте сессию, филиал и подтверждённый автомобиль.',
    UNAVAILABLE:'Не опубликовано или закрыто для записи.',
    RATE_LIMITED:'Слишком много запросов. Повторите позже.',
    CHALLENGE_INVALID:'Проверка устарела или ответ неверен. Обновите вопрос.',
    SLOT_TAKEN:'Время уже занято. Выберите другое.',
    LOCATION_CLOSED:'Филиал закрыт в это время.',
    ALREADY_CLAIMED:'Заявка уже подтверждена другим ключом. Обновите статус.',
    CLIENT_VERIFICATION_REQUIRED:'Ожидается вход клиента и verified-связь с автомобилем.',
    FULFILL_CONFLICT:'Пост занят, материалы недоступны или изменены права. Выберите новый процесс.',
    INVALID_INPUT:'Проверьте UUID, дату, материалы и обязательные согласия.',
  };
  return labels[c]??('Операция отклонена: '+c);
}

export function AutoPublicBooking({mode}:{mode:Mode}){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [offers,setOffers]=React.useState<Offer[]>([]);
  const [selected,setSelected]=React.useState('');
  const [day,setDay]=React.useState(dayNow);
  const [slots,setSlots]=React.useState<Slot[]>([]);
  const [slot,setSlot]=React.useState('');
  const [question,setQuestion]=React.useState('');
  const [challengeId,setChallengeId]=React.useState('');
  const [answer,setAnswer]=React.useState('');
  const [processing,setProcessing]=React.useState(false);
  const [marketing,setMarketing]=React.useState(false);
  const [receipt,setReceipt]=React.useState<Receipt|null>(null);
  const [requestId,setRequestId]=React.useState('');
  const [receiptKey,setReceiptKey]=React.useState('');
  const [vehicleLink,setVehicleLink]=React.useState('');
  const [queue,setQueue]=React.useState<Item[]>([]);
  const [bayId,setBayId]=React.useState('');
  const [serviceId,setServiceId]=React.useState('');
  const [policy,setPolicy]=React.useState('DEV-CONSENT-2026');
  const [materialsText,setMaterialsText]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [busy,setBusy]=React.useState(false);
  const [error,setError]=React.useState('');
  const [message,setMessage]=React.useState('');
  const [status,setStatus]=React.useState('');
  const activeOffer=offers.find(x=>x.id===selected);
  const staffPath='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/public-booking';

  async function read(url:string){
    setError('');setMessage('');
    try{
      const r=await fetch(url,{credentials:'include',cache:'no-store'});
      if(r.status===403||r.status===401){
        setOffers([]);setQueue([]);setSlots([]);setStatus('');
        setError('Доступ запрещён: проверьте сессию и полномочия.');
        return null;
      }
      if(!r.ok)throw new Error(codeMessage(String((await r.json()).code??r.status)));
      return await r.json() as unknown;
    }catch(e){setError(String(e));return null;}
  }
  async function mutate(url:string,body:Record<string,unknown>,auth:boolean){
    if(busy||pending)return;
    const entry={url,body:JSON.stringify(body),auth};
    setPending(entry);
    await send(entry);
  }
  async function send(entry:Pending){
    if(busy)return;
    setBusy(true);setError('');setMessage('');
    try{
      let csrf:string|null=null;
      if(entry.auth){
        const response=await fetch('/api/v1/auth/csrf',{credentials:'include',
          cache:'no-store'});
        if(!response.ok)throw new Error('Нет действующей сессии/CSRF');
        const parsed:unknown=await response.json();
        const value=(parsed as {csrfToken?:unknown})?.csrfToken;
        if(typeof value!=='string'||!value)throw new Error('Недопустимый CSRF');
        csrf=value;
      }
      const response=await fetch(entry.url,{
        method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})},
        body:entry.body,
      });
      if([401,403].includes(response.status)){
        setOffers([]);setQueue([]);setSlots([]);
      }
      if(!response.ok){
        let code=String(response.status);
        try{const data=await response.json() as {code?:unknown};
          if(typeof data.code==='string')code=data.code;}catch{/* error body optional */}
        setPending(null);setError(codeMessage(code));return;
      }
      const data:unknown=await response.json();
      setPending(null);
      const shape=data as Record<string,unknown>;
      if(entry.url.endsWith('/challenges')){
        if(!uuid.test(String(shape.challengeId))||typeof shape.question!=='string')
          throw new Error('Недостоверный проверочный вопрос');
        setChallengeId(String(shape.challengeId));
        setQuestion(shape.question);
        setMessage('Проверка получена. Введите ответ и отправьте заявку.');
      }else if(entry.url===pub+'/requests'){
        if(!uuid.test(String(shape.requestId))||!uuid.test(String(shape.receiptKey)))
          throw new Error('Недостоверный ответ по заявке');
        const owned={requestId:String(shape.requestId),receiptKey:String(shape.receiptKey)};
        setReceipt(owned);setRequestId(owned.requestId);setReceiptKey(owned.receiptKey);
        setStatus('AWAITING_VERIFICATION');
        setMessage('Заявка создана, время НЕ зарезервировано. Скопируйте оба ключа.');
      }else if(entry.url.endsWith('/claim')||entry.url.endsWith('/withdraw')||
               entry.url.endsWith('/status')){
        if(typeof shape.status!=='string')throw new Error('Недостоверный статус');
        setStatus(shape.status);
        setMessage('Текущий статус: '+shape.status+
          (shape.marketingEnabled===false?' · маркетинг отключён':''));
      }else if(entry.url.endsWith('/fulfill')){
        if(!uuid.test(String(shape.appointmentId)))throw new Error('Недостоверная запись');
        setMessage('Запись создана: '+shape.appointmentId+
          ' (только dev, реальные уведомления запрещены).');
        const loaded=await read(staffPath+'/queue');
        if(loaded)setQueue(parseQueue(loaded));
      }else if(entry.url.endsWith('/offers')){
        if(!uuid.test(String(shape.id)))throw new Error('Недостоверная публикация');
        setMessage('Опубликована услуга '+shape.id+'. Можно открыть публичный виджет.');
        const loaded=await read(staffPath+'/offers');
        if(loaded)setOffers(parseOffers(loaded));
      }
    }catch(e){
      // Network failure: preserve exact body/requestKey; retry MUST NOT create a new key.
      setError(String(e)+' · Повторите исходный запрос тем же ключом.');
    }finally{setBusy(false);}
  }
  async function loadOffers(staff=false){
    if(!uuid.test(org)||!uuid.test(loc)){setError('Укажите UUID организации и филиала.');return;}
    const url=staff?staffPath+'/offers':pub+'/offers?organizationId='+org+'&locationId='+loc;
    const result=await read(url);
    if(result){
      try{const items=parseOffers(result);setOffers(items);setSelected(items[0]?.id??'');
        setMessage('Предложений: '+items.length);}catch(e){setOffers([]);setError(String(e));}
    }
  }
  async function loadSlots(){
    if(!uuid.test(selected)){setError('Выберите опубликованную услугу.');return;}
    setSlots([]);setSlot('');setQuestion('');setChallengeId('');
    const data=await read(pub+'/slots?offerId='+selected+'&localDay='+day);
    if(data){try{const items=parseSlots(data);setSlots(items);
      setMessage('Свободных кандидатов: '+items.length+' (без гарантии резерва).');
    }catch(e){setError(String(e));}}
  }
  async function refreshQueue(){
    if(!uuid.test(org)||!uuid.test(loc)){setError('Введите организацию и филиал.');return;}
    const data=await read(staffPath+'/queue');
    if(data){try{setQueue(parseQueue(data));}catch(e){setQueue([]);setError(String(e));}}
  }
  async function loadStatus(){
    if(!uuid.test(requestId)||!uuid.test(receiptKey)){
      setError('Укажите ID заявки и секретный ключ квитанции.');return;
    }
    await mutate(pub+'/requests/'+requestId+'/status',{receiptKey},false);
  }
  function parseMaterials():{skuId:string;kind:'PART'|'CONSUMABLE';quantity:number}[]{
    const lines=materialsText.trim()?materialsText.trim().split(/\r?\n/):[];
    if(lines.length>8)throw new Error('Не более восьми материалов');
    const items=lines.map(line=>{
      const values=line.split(',').map(x=>x.trim());
      const qty=Number(values[2]);
      if(values.length!==3||!uuid.test(values[0])||
         !['PART','CONSUMABLE'].includes(values[1])||
         !Number.isSafeInteger(qty)||qty<=0||qty>1e9)
        throw new Error('Формат: UUID,PART|CONSUMABLE,количество');
      return {skuId:values[0],kind:values[1] as 'PART'|'CONSUMABLE',quantity:qty};
    });
    if(new Set(items.map(i=>i.skuId)).size!==items.length)
      throw new Error('SKU не должны повторяться');
    return items;
  }
  function publish(){
    if(!uuid.test(org)||!uuid.test(loc)||!uuid.test(bayId)||!uuid.test(serviceId)){
      setError('Укажите UUID филиала, поста и услуги.');return;
    }
    try{const materials=parseMaterials();
      void mutate(staffPath+'/offers',{
        requestKey:newKey(),bayId,serviceId,policyVersion:policy,materials},true);
    }catch(e){setError(String(e));}
  }
  return <section aria-label={mode==='public'?'Публичная онлайн-запись (только DEV)':
    mode==='client'?'Подтверждение публичной заявки клиентом (DEV)':
      'Публикации и публичные заявки СТО (DEV)'}>
    <h1>{mode==='public'?'Запись на СТО':
        mode==='client'?'Подтвердить заявку на свой автомобиль':
          'Публичные заявки: публикация, материалы, запись'}</h1>
    <p role="note">Синтетический стенд. Не отправляйте реальные данные, VIN, телефоны,
      сообщения или ФИО. Нет реальной рассылки, фискализации или юридического
      согласия. Публичная заявка НЕ держит слот; запись и резерв создаются после
      проверки клиента сотрудником.</p>
    {error&&<p role="alert">{error}</p>}
    {message&&<p role="status">{message}</p>}
    {pending&&<p><button type="button" disabled={busy}
      onClick={()=>void send(pending)}>Повторить исходный запрос</button>
      <button type="button" disabled={busy} onClick={()=>setPending(null)}>
        Отменить повтор</button></p>}

    {mode!=='client'&&<fieldset>
      <legend>Филиал</legend>
      <label>UUID организации <input value={org} onChange={e=>{
        setOrg(e.target.value);setOffers([]);setQueue([]);setSlots([]);}} /></label>
      <label>UUID филиала <input value={loc} onChange={e=>{
        setLoc(e.target.value);setOffers([]);setQueue([]);setSlots([]);}} /></label>
    </fieldset>}

    {mode==='public'&&<>
      <section>
        <h2>1. Опубликованные услуги и свободное время</h2>
        <button type="button" disabled={busy} onClick={()=>void loadOffers()}>
          Найти услуги</button>
        <label>Услуга и пост
          <select value={selected} onChange={e=>{setSelected(e.target.value);
            setSlots([]);setSlot('');setQuestion('');}}>
            <option value="">Выберите услугу</option>
            {offers.map(o=><option value={o.id} key={o.id}>
              {o.serviceTitle} · пост {o.bayCode} · {o.durationMinutes} мин
            </option>)}
          </select>
        </label>
        {activeOffer&&<p>Материалы: {activeOffer.materialsReady?'условно доступны':
          'нужна проверка склада'}. Согласие: {activeOffer.policyVersion}.
          Все доступности предварительные.</p>}
        <label>Дата <input type="date" value={day} onChange={e=>setDay(e.target.value)} /></label>
        <button type="button" disabled={!selected||busy} onClick={()=>void loadSlots()}>
          Показать время</button>
        <label>Время
          <select value={slot} onChange={e=>{setSlot(e.target.value);
            setQuestion('');setChallengeId('');}}>
            <option value="">Выберите время</option>
            {slots.map(x=><option value={x.startsAt} key={x.startsAt}>
              {localTime(x.startsAt,activeOffer?.timeZone??'UTC')}</option>)}
          </select>
        </label>
      </section>
      <section>
        <h2>2. Заявка без персональных данных</h2>
        <label><input type="checkbox" checked={processing}
          onChange={e=>setProcessing(e.target.checked)} />
          Подтверждаю ознакомление с тестовыми условиями обработки заявки
          (обязательно, не юридическое согласие)</label>
        <label><input type="checkbox" checked={marketing}
          onChange={e=>setMarketing(e.target.checked)} />
          Необязательный выбор маркетинговых коммуникаций (отправки НЕТ)</label>
        <button type="button" disabled={!slot||busy}
          onClick={()=>void mutate(pub+'/challenges',{offerId:selected},false)}>
          Получить проверочный вопрос</button>
        {challengeId&&<>
          <label>Антибот-вопрос DEV: {question}
            <input value={answer} inputMode="numeric"
              onChange={e=>setAnswer(e.target.value)} /></label>
          <button type="button" disabled={!processing||busy||!/^\d{1,2}$/.test(answer)}
            onClick={()=>{const chosen=slots.find(s=>s.startsAt===slot);
              if(!chosen)return;
              void mutate(pub+'/requests',{
                offerId:selected,requestKey:newKey(),challengeId,
                answer:Number(answer),startsAt:chosen.startsAt,endsAt:chosen.endsAt,
                processingConsent:processing,marketingConsent:marketing},false);
            }}>Оставить заявку (не бронировать слот)</button>
        </>}
        {receipt&&<section aria-label="Секретная квитанция заявки">
          <h3>Квитанция — сохраните локально</h3>
          <p>ID заявки: <code>{receipt.requestId}</code></p>
          <p>Секретный ключ квитанции: <code>{receipt.receiptKey}</code></p>
          <p>Этот ключ необходим для подтверждения. Не пересылайте его третьим лицам.
            Для продолжения откройте <a href="/account/auto-public-booking">
              подтверждение в личном кабинете</a> и войдите под исходным клиентом.</p>
        </section>}
      </section>
    </>}

    {mode==='client'&&<>
      <h2>Проверка своей verified-связи</h2>
      <p>Авторизуйтесь как CLIENT. Нельзя вводить чужие customer ID/VIN;
        нужен только ID уже подтверждённой связи с автомобилем.</p>
      <label>ID заявки <input value={requestId}
        onChange={e=>setRequestId(e.target.value)} /></label>
      <label>Секретный ключ квитанции <input value={receiptKey}
        onChange={e=>setReceiptKey(e.target.value)} /></label>
      <label>UUID моей verified-связи <input value={vehicleLink}
        onChange={e=>setVehicleLink(e.target.value)} /></label>
      <button type="button" disabled={busy} onClick={()=>void loadStatus()}>
        Обновить состояние заявки</button>
      <button type="button" disabled={busy||!uuid.test(requestId)||
        !uuid.test(receiptKey)||!uuid.test(vehicleLink)}
        onClick={()=>void mutate(pub+'/requests/'+requestId+'/claim',{
          receiptKey,claimKey:newKey(),customerLinkId:vehicleLink,
          processingConsent:true},true)}>
        Подтвердить владельца и обработку заявки (не бронировать слот)</button>
      <button type="button" disabled={busy||!uuid.test(requestId)||!uuid.test(receiptKey)}
        onClick={()=>void mutate(pub+'/requests/'+requestId+'/withdraw',{
          receiptKey,withdrawKey:newKey()},true)}>
        Отозвать тестовый выбор маркетинга</button>
      {status&&<p role="status">Заявка: {status}.</p>}
    </>}

    {mode==='staff'&&<>
      <section>
        <h2>1. Публикация услуги (MANAGER; с материалами ещё WAREHOUSE)</h2>
        <p>Укажите заранее подготовленные UUID своего поста и услуги. Не раскрывайте
          публично складские SKU и количество.</p>
        <label>UUID поста <input value={bayId} onChange={e=>setBayId(e.target.value)} /></label>
        <label>UUID услуги <input value={serviceId}
          onChange={e=>setServiceId(e.target.value)} /></label>
        <label>Версия условий <input value={policy}
          onChange={e=>setPolicy(e.target.value)} /></label>
        <label>Материалы (каждый в новой строке: UUID,PART|CONSUMABLE,количество)
          <textarea rows={3} value={materialsText}
            onChange={e=>setMaterialsText(e.target.value)} /></label>
        <button type="button" disabled={busy} onClick={publish}>Опубликовать</button>
        <button type="button" disabled={busy} onClick={()=>void loadOffers(true)}>
          Показать публикации</button>
        {offers.map(o=><p key={o.id}>{o.serviceTitle} — {o.bayCode} — <code>{o.id}</code>
          {' '}· {o.policyVersion}</p>)}
      </section>
      <section>
        <h2>2. Заявки (INTAKE, для резервов дополнительно WAREHOUSE)</h2>
        <button type="button" disabled={busy} onClick={()=>void refreshQueue()}>
          Обновить очередь</button>
        <ol>{queue.map(item=><li key={item.requestId}>
          <p>{item.status} · {item.startsAt} · <code>{item.requestId}</code></p>
          <p>Материалы: {item.materials.length?item.materials.map(m=>
             m.kind+' '+m.skuId+' ×'+m.quantity).join(' · '):'не требуются'}.</p>
          {item.status==='WAITING_FOR_STAFF'&&
            <button type="button" disabled={busy}
              onClick={()=>void mutate(staffPath+'/requests/'+item.requestId+'/fulfill',
                {requestKey:newKey()},true)}>
              Создать запись, CRM-лид и резерв атомарно</button>}
          {item.appointmentId&&<p>Запись: <code>{item.appointmentId}</code></p>}
        </li>)}</ol>
      </section>
    </>}
  </section>;
}
