import React from 'react';
import { Card, Alert, FormField, Button } from '../../ui';

async function post(path:string,body:object):Promise<string>{
  const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store'});
  if(!csrf.ok)throw new Error('401 — локальная сессия недоступна');
  const value:unknown=await csrf.json();
  if(!value||typeof value!=='object'||!('csrfToken' in value)||typeof value.csrfToken!=='string')
    throw new Error('CSRF недоступен');
  const response=await fetch(path,{method:'POST',credentials:'include',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':value.csrfToken,
      'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)});
  if(response.status===409)throw new Error('409 — конфликт версии или ограничения FSM');
  if([401,403,404].includes(response.status))throw new Error(response.status+' — доступ запрещён');
  if(!response.ok)throw new Error('Ошибка '+response.status);
  const result:unknown=await response.json();
  if(!result||typeof result!=='object'||!('id' in result)||typeof result.id!=='string')
    throw new Error('Некорректный ответ');
  return result.id;
}
function StaffForm({title,button,run}:{title:string;button:string;run:()=>Promise<string>}){
  const [state,setState]=React.useState('');const [busy,setBusy]=React.useState(false);
  return <div className="crm-action"><h3>{title}</h3><Button type="button" disabled={busy} onClick={()=>{
    setBusy(true);setState('');
    void run().then(id=>setState('Операция записана: '+id))
      .catch(e=>setState(e instanceof Error?e.message:'Ошибка операции'))
      .finally(()=>setBusy(false));
  }}>{button}</Button>{state&&<p role="status">{state}</p>}</div>;
}
export function StaffEstimates(){
  const [ticket,setTicket]=React.useState('');
  const [version,setVersion]=React.useState('1');
  const [description,setDescription]=React.useState('Диагностика (синтетика)');
  const [amount,setAmount]=React.useState('1000');
  const [estimate,setEstimate]=React.useState('');
  const [estimateVersion,setEstimateVersion]=React.useState('1');
  return <section className="crm-operation-panel" aria-label="Работа со сметами"><Card>
    <h2>Сметы</h2>
    <p>Все изменения требуют отдельного разрешения на заявку. Не используйте реальные данные.</p>
    <FormField label="UUID заявки" id="estimate-ticket"><input id="estimate-ticket" value={ticket} onChange={e=>setTicket(e.target.value)} /></FormField>
    <FormField label="Версия заявки" id="estimate-version-ticket"><input id="estimate-version-ticket" type="number" min="1" value={version} onChange={e=>setVersion(e.target.value)}/></FormField>
    <FormField label="Описание работы" id="estimate-description"><input id="estimate-description" value={description} onChange={e=>setDescription(e.target.value)}/></FormField>
    <FormField label="Сумма, копейки" id="estimate-amount"><input id="estimate-amount" type="number" min="0" value={amount} onChange={e=>setAmount(e.target.value)}/></FormField>
    <StaffForm title="Новая версия сметы" button="Создать смету" run={()=>post(
      '/api/v1/dev/tickets/'+encodeURIComponent(ticket)+'/estimates',
      {expectedTicketVersion:Number(version),lines:[{description,amountMinor:Number(amount)}]})}/>
    <FormField label="UUID сметы" id="estimate-id"><input id="estimate-id" value={estimate} onChange={e=>setEstimate(e.target.value)}/></FormField>
    <FormField label="Версия сметы" id="estimate-version"><input id="estimate-version" type="number" min="1" value={estimateVersion} onChange={e=>setEstimateVersion(e.target.value)}/></FormField>
    <StaffForm title="Отправка точной версии" button="Отправить смету" run={()=>post(
      '/api/v1/dev/estimates/'+encodeURIComponent(estimate)+'/send',
      {expectedVersion:Number(estimateVersion)})}/>
  </Card></section>;
}
export function StaffStock(){
  const [ticket,setTicket]=React.useState('');const [lot,setLot]=React.useState('');
  const [quantity,setQuantity]=React.useState('1');const [reservation,setReservation]=React.useState('');
  return <section className="crm-operation-panel" aria-label="Операции склада"><Card>
    <h2>Склад</h2>
    <p>Резервирование разрешается только для подтверждённой совместимости и живого ACL.</p>
    <FormField label="UUID заявки" id="stock-ticket"><input id="stock-ticket" value={ticket} onChange={e=>setTicket(e.target.value)}/></FormField>
    <FormField label="UUID партии" id="stock-lot"><input id="stock-lot" value={lot} onChange={e=>setLot(e.target.value)}/></FormField>
    <FormField label="Количество" id="stock-quantity"><input id="stock-quantity" type="number" min="1" value={quantity} onChange={e=>setQuantity(e.target.value)}/></FormField>
    <StaffForm title="Атомарный резерв" button="Зарезервировать" run={()=>post(
      '/api/v1/dev/inventory/reservations',{ticketId:ticket,lotId:lot,quantity:Number(quantity)})}/>
    <FormField label="UUID резерва" id="stock-reservation"><input id="stock-reservation" value={reservation} onChange={e=>setReservation(e.target.value)}/></FormField>
    <StaffForm title="Отмена резерва" button="Отменить резерв" run={()=>post(
      '/api/v1/dev/inventory/reservations/'+encodeURIComponent(reservation)+'/cancel',{})}/>
  </Card></section>;
}
