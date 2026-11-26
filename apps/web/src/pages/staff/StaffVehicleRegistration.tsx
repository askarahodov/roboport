import React from 'react';
import { Button, FormField } from '../../ui';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const validId=(v:string)=>uuid.test(v.trim());
const validText=(v:string,max:number)=>v===v.trim()&&v.length>=1&&v.length<=max&&
  !Array.from(v).some(c=>c.charCodeAt(0)<32);
type Fields={make:string;model:string;variant:string;year:string};
type Pending={path:string;key:string;body:Record<string,unknown>;create:boolean};
type Mutate=(path:string,body?:Record<string,unknown>,key?:string)=>Promise<unknown>;
const empty=():Fields=>({make:'',model:'',variant:'',year:''});
function record(raw:unknown):Record<string,unknown>{
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw Error('response');
  return raw as Record<string,unknown>;
}
const revision=(value:unknown):value is number=>typeof value==='number'&&
  Number.isInteger(value)&&value>=1&&value<=1000000;

export function StaffVehicleRegistration({mutate,onRegistered,onCleared}:{
  mutate:Mutate;onRegistered:(vehicle:string,link:string)=>void;onCleared:()=>void;
}){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [customer,setCustomer]=React.useState(''),[vin,setVin]=React.useState('');
  const [vehicle,setVehicle]=React.useState(''),[current,setCurrent]=React.useState<number|null>(null);
  const [editable,setEditable]=React.useState(false),[masked,setMasked]=React.useState<string|null>(null);
  const [form,setForm]=React.useState<Fields>(empty);
  const [mode,setMode]=React.useState<'create'|'edit'>('create');
  const [pending,setPending]=React.useState<Pending|null>(null),[busy,setBusy]=React.useState(false);
  const [message,setMessage]=React.useState<{ok:boolean;text:string}|null>(null);
  const inFlight=React.useRef(false);
  const scope=validId(org)&&validId(loc);
  const locked=busy||pending!==null;
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+'/vehicle-registrations';
  const year=form.year===''?null:Number(form.year);
  const valid=validText(form.make,100)&&validText(form.model,120)&&
    (form.variant===''||validText(form.variant,160))&&(year===null||(
      /^[0-9]{4}$/.test(form.year)&&year>=1886&&year<=2100));
  function clear(){
    onCleared();setCustomer('');setVin('');setVehicle('');setCurrent(null);setEditable(false);
    setMasked(null);setForm(empty());setMessage(null);
  }
  function changeVehicle(value:string){
    onCleared();setVehicle(value);setCurrent(null);setEditable(false);setMasked(null);
    setForm(empty());setMessage(null);
  }
  async function load(){
    if(!scope||!validId(vehicle)||locked||inFlight.current)return;
    inFlight.current=true;setBusy(true);setMessage(null);setCurrent(null);setEditable(false);setMasked(null);setForm(empty());
    try{
      const response=await fetch(base+'/'+encodeURIComponent(vehicle.trim())+'?'+
        new URLSearchParams({locationId:loc.trim()}),{credentials:'include',cache:'no-store'});
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=record(await response.json());
      if(data.vehicleId!==vehicle.trim().toLowerCase()||!revision(data.revision)||
        typeof data.make!=='string'||typeof data.model!=='string'||
        !(data.variant===null||typeof data.variant==='string')||
        !(data.modelYear===null||(typeof data.modelYear==='number'&&Number.isInteger(data.modelYear)))||
        !(data.vinLast4===null||(typeof data.vinLast4==='string'&&/^[A-HJ-NPR-Z0-9]{4}$/.test(data.vinLast4)))||
        typeof data.editable!=='boolean')throw Error('response');
      setForm({make:data.make,model:data.model,variant:data.variant??'',year:data.modelYear===null?'':String(data.modelYear)});
      setCurrent(data.revision);setMasked(data.vinLast4);setEditable(data.editable);
      if(!data.editable)setMessage({ok:false,text:'Правка недоступна: проверка владения уже началась или связь неактивна.'});
    }catch{
      onCleared();setMessage({ok:false,text:'Карточка недоступна. Проверьте филиал, сессию и полномочия.'});
    }finally{inFlight.current=false;setBusy(false);}
  }
  async function send(snap:Pending){
    if(inFlight.current)return;
    inFlight.current=true;setPending(snap);setBusy(true);setMessage(null);
    try{
      const data=record(await mutate(snap.path,snap.body,snap.key));
      if(typeof data.vehicleId!=='string'||!validId(data.vehicleId)||
        typeof data.linkId!=='string'||!validId(data.linkId)||!revision(data.revision)||
        data.status!=='PENDING'||(snap.create?data.revision!==1:
          data.vehicleId!==vehicle.trim().toLowerCase()||data.revision!==Number(snap.body.expectedRevision)+1))
        throw Error('response');
      setPending(null);setVin('');setCustomer('');
      setVehicle(data.vehicleId);setCurrent(data.revision);setEditable(true);setMode('edit');
      if(snap.create){
        setMasked(typeof snap.body.vin==='string'?snap.body.vin.slice(-4):null);
        onRegistered(data.vehicleId,data.linkId);
      }
      setMessage({ok:true,text:snap.create?'Автомобиль зарегистрирован. Связь ожидает независимого подтверждения.':
        'Новая версия карточки сохранена. VIN и связь не изменены.'});
    }catch(error){
      const code=error instanceof Error?error.message:'';
      if(/^HTTP 4[0-9]{2}$/.test(code)){
        onCleared();setPending(null);setCurrent(null);setEditable(false);setMasked(null);setForm(empty());setVin('');setCustomer('');
        setMessage({ok:false,text:'Операция отклонена. Проверьте права, состояние и конфликт VIN или версии; перечитайте карточку.'});
      }else{
        setMessage({ok:false,text:'Результат неизвестен. Повторите тот же запрос с прежним ключом.'});
      }
    }finally{inFlight.current=false;setBusy(false);}
  }
  function submit(){
    if(!scope||!valid||locked||inFlight.current)return;
    const body={locationId:loc.trim(),make:form.make,model:form.model,
      variant:form.variant||null,modelYear:year};
    if(mode==='create'){
      if(!validId(customer)||(vin!==''&&!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)))return;
      void send({path:base,key:crypto.randomUUID(),create:true,body:{...body,customerId:customer.trim(),vin:vin||null}});
    }else if(current!==null&&editable&&validId(vehicle)){
      void send({path:base+'/'+encodeURIComponent(vehicle.trim())+'/revisions',
        key:crypto.randomUUID(),create:false,body:{...body,expectedRevision:current}});
    }
  }
  const createReady=validId(customer)&&(vin===''||/^[A-HJ-NPR-Z0-9]{17}$/.test(vin));
  return <section aria-label="Регистрация автомобиля в филиале" className="auto-claim-panel vehicle-registration">
    <h4>Регистрация автомобиля в филиале (dev)</h4>
    <p>Только синтетические данные. Новый автомобиль и ожидающая связь создаются вместе.
      Правка описания возможна до выдачи первого кода; VIN и филиал неизменяемы.</p>
    <form onSubmit={e=>{e.preventDefault();submit();}}>
      <fieldset disabled={locked}>
        <legend>Организация и филиал</legend>
        <FormField label="UUID организации регистрации" id="reg-org">
          <input id="reg-org" value={org} onChange={e=>{setOrg(e.target.value);clear();}} autoComplete="off"/>
        </FormField>
        <FormField label="UUID филиала регистрации" id="reg-loc">
          <input id="reg-loc" value={loc} onChange={e=>{setLoc(e.target.value);clear();}} autoComplete="off"/>
        </FormField>
        <Button type="button" variant="secondary" onClick={()=>{clear();setMode('create');}}>Новый автомобиль</Button>{' '}
        <Button type="button" variant="secondary" onClick={()=>{clear();setMode('edit');}}>Загрузить существующую регистрацию</Button>
        {mode==='create'?<>
          <FormField label="UUID активного клиента (регистрация)" id="reg-customer">
            <input id="reg-customer" value={customer} onChange={e=>setCustomer(e.target.value)} autoComplete="off"/>
          </FormField>
          <FormField label="VIN нового автомобиля (необязательно)" id="reg-vin">
            <input id="reg-vin" value={vin} maxLength={17} onChange={e=>setVin(e.target.value.toUpperCase())} autoComplete="off"/>
          </FormField>
        </>:<>
          <FormField label="UUID зарегистрированного автомобиля" id="reg-vehicle">
            <input id="reg-vehicle" value={vehicle} onChange={e=>changeVehicle(e.target.value)} autoComplete="off"/>
          </FormField>
          <Button type="button" variant="secondary" disabled={!scope||!validId(vehicle)} onClick={()=>void load()}>Прочитать карточку регистрации</Button>
          {current!==null&&<p>Версия: {current}. {masked?'VIN ••••'+masked:'VIN не указан'}.</p>}
        </>}
        {(mode==='create'||current!==null)&&<>
          <FormField label="Марка автомобиля" id="reg-make"><input id="reg-make" maxLength={100}
            value={form.make} disabled={mode==='edit'&&!editable} onChange={e=>setForm({...form,make:e.target.value})}/></FormField>
          <FormField label="Модель автомобиля" id="reg-model"><input id="reg-model" maxLength={120}
            value={form.model} disabled={mode==='edit'&&!editable} onChange={e=>setForm({...form,model:e.target.value})}/></FormField>
          <FormField label="Модификация автомобиля" id="reg-variant"><input id="reg-variant" maxLength={160}
            value={form.variant} disabled={mode==='edit'&&!editable} onChange={e=>setForm({...form,variant:e.target.value})}/></FormField>
          <FormField label="Модельный год автомобиля" id="reg-year"><input id="reg-year" inputMode="numeric" maxLength={4}
            value={form.year} disabled={mode==='edit'&&!editable} onChange={e=>setForm({...form,year:e.target.value})}/></FormField>
        </>}
        <Button type="submit" disabled={!scope||!valid||locked||
          (mode==='create'?!createReady:current===null||!editable||!validId(vehicle))}>
          {mode==='create'?'Зарегистрировать автомобиль и связь':'Сохранить новую версию карточки'}
        </Button>
      </fieldset>
    </form>
    {pending&&<Button type="button" disabled={busy} onClick={()=>void send(pending)}>Повторить регистрацию с прежним ключом</Button>}
    {message&&<p role={message.ok?'status':'alert'}>{message.text}</p>}
  </section>;
}
