import React from 'react';

type StageCode='NEW'|'QUALIFIED'|'BOOKED'|'LOST';
type LossReason='PRICE'|'TIMING'|'NO_RESPONSE'|'OTHER';
type Stage={code:'QUALIFIED'|'BOOKED'|'LOST';label:string;position:number;
  enabled:boolean;allowedFrom:StageCode[];lossReasons:LossReason[]};
type Column={code:StageCode;label:string;position:number;enabled:boolean;
  allowedFrom:StageCode[];lossReasons:LossReason[]};
type Item={leadId:string;customerLinkId:string;ownerId:string;sourceCode:'WEB'|'PHONE'|'WALK_IN';
  stageCode:StageCode;createdAt:string};
type Board={syntheticOnly:true;version:number;columns:Column[];items:Item[];limit:200;canConfigure:boolean};
type Workflow={syntheticOnly:true;version:number;canConfigure:boolean;config:{stages:Stage[]}};
type PendingMutation={url:string;body:Record<string,unknown>;success:string};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const codes:StageCode[]=['NEW','QUALIFIED','BOOKED','LOST'];
const reasons:LossReason[]=['PRICE','TIMING','NO_RESPONSE','OTHER'];
const reasonNames:Record<LossReason,string>={PRICE:'Стоимость',TIMING:'Сроки',
  NO_RESPONSE:'Нет ответа',OTHER:'Другое'};
const sourceNames={WEB:'Сайт',PHONE:'Звонок',WALK_IN:'Личное обращение'};
const defaultStages:Stage[]=[
  {code:'QUALIFIED',label:'Обработано',position:10,enabled:true,allowedFrom:['NEW'],lossReasons:[]},
  {code:'BOOKED',label:'Записан',position:20,enabled:true,allowedFrom:['QUALIFIED'],lossReasons:[]},
  {code:'LOST',label:'Отказ',position:30,enabled:true,allowedFrom:['NEW','QUALIFIED'],
    lossReasons:['PRICE','TIMING','NO_RESPONSE','OTHER']},
];
function parseWorkflow(raw:unknown):Workflow{
  if(!raw||typeof raw!=='object')throw Error('workflow');
  const x=raw as Partial<Workflow>;
  if(x.syntheticOnly!==true||!Number.isSafeInteger(x.version)||typeof x.canConfigure!=='boolean'||!x.config||
    !Array.isArray(x.config.stages)||x.config.stages.length!==3)throw Error('workflow');
  const stages=x.config.stages;
  if(!['QUALIFIED','BOOKED','LOST'].every(code=>stages.some(s=>s.code===code))||
    !stages.every(s=>s&&['QUALIFIED','BOOKED','LOST'].includes(s.code)&&
      typeof s.label==='string'&&s.label.length>0&&s.label.length<=60&&
      Number.isInteger(s.position)&&s.position>=0&&s.position<=99&&
      typeof s.enabled==='boolean'&&Array.isArray(s.allowedFrom)&&
      Array.isArray(s.lossReasons)&&s.lossReasons.every(r=>reasons.includes(r))))
    throw Error('workflow');
  return x as Workflow;
}
function parseBoard(raw:unknown):Board{
  if(!raw||typeof raw!=='object')throw Error('board');
  const x=raw as Partial<Board>;
  if(x.syntheticOnly!==true||!Number.isSafeInteger(x.version)||typeof x.canConfigure!=='boolean'||x.limit!==200||
    !Array.isArray(x.columns)||x.columns.length!==4||
    !Array.isArray(x.items)||x.items.length>200)throw Error('board');
  if(!x.columns.every(c=>c&&codes.includes(c.code)&&typeof c.label==='string'&&
    c.label.length>0&&c.label.length<=60&&Number.isInteger(c.position)&&
    typeof c.enabled==='boolean'&&Array.isArray(c.allowedFrom)&&
    c.allowedFrom.every(v=>codes.includes(v))&&Array.isArray(c.lossReasons)&&
    c.lossReasons.every(v=>reasons.includes(v)))||
    new Set(x.columns.map(c=>c.code)).size!==4||
    !x.items.every(i=>i&&uuid.test(i.leadId)&&uuid.test(i.customerLinkId)&&
      uuid.test(i.ownerId)&&['WEB','PHONE','WALK_IN'].includes(i.sourceCode)&&
      codes.includes(i.stageCode)&&typeof i.createdAt==='string'&&
      !Number.isNaN(Date.parse(i.createdAt))))throw Error('board');
  return x as Board;
}
async function csrfToken(){
  const response=await fetch('/api/v1/auth/csrf',{method:'POST',
    credentials:'include',cache:'no-store'});
  if(!response.ok)throw Error('csrf');
  const value:unknown=await response.json();
  if(!value||typeof value!=='object'||!('csrfToken' in value)||
    typeof value.csrfToken!=='string')throw Error('csrf');
  return value.csrfToken;
}

export function AutoCrmKanban(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [workflow,setWorkflow]=React.useState<Workflow|null>(null);
  const [stages,setStages]=React.useState<Stage[]>(defaultStages);
  const [board,setBoard]=React.useState<Board|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [message,setMessage]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [editLead,setEditLead]=React.useState('');
  const [moveStage,setMoveStage]=React.useState<StageCode>('QUALIFIED');
  const [appointment,setAppointment]=React.useState('');
  const [reason,setReason]=React.useState<LossReason>('PRICE');
  const [assignees,setAssignees]=React.useState<Record<string,string>>({});
  const [revision,setRevision]=React.useState(0);
  const [pending,setPending]=React.useState<PendingMutation|null>(null);
  const base=uuid.test(org)&&uuid.test(loc)?
    '/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+'/locations/'+encodeURIComponent(loc)+'/crm':'';

  async function load(){
    if(!base)return;
    setBusy(true);setMessage('');setStatus('loading');setBoard(null);setWorkflow(null);
    try{
      const [w,b]=await Promise.all([
        fetch(base+'/workflow',{credentials:'include',cache:'no-store'}),
        fetch(base+'/kanban',{credentials:'include',cache:'no-store'}),
      ]);
      if([401,403,404].includes(w.status)||[401,403,404].includes(b.status)){
        setStatus('denied');return;
      }
      if(!w.ok||!b.ok)throw Error('http');
      const [rawW,rawB]=await Promise.all([w.json(),b.json()]);
      const parsedW=parseWorkflow(rawW),parsedB=parseBoard(rawB);
      setWorkflow(parsedW);setStages(parsedW.config.stages);setBoard(parsedB);setStatus('ready');
    }catch{setStatus('error');setBoard(null);setWorkflow(null);}
    finally{setBusy(false);}
  }

  async function submitMutation(item:PendingMutation){
    if(!base||busy)return;
    setBusy(true);setMessage('');
    try{
      const token=await csrfToken();
      const response=await fetch(item.url,{method:'POST',credentials:'include',cache:'no-store',
        headers:{'Content-Type':'application/json','X-CSRF-Token':token},
        body:JSON.stringify(item.body)});
      if(response.status===403){setPending(null);setStatus('denied');setBoard(null);setWorkflow(null);return;}
      if(response.status===409){setPending(null);setMessage('Изменилось состояние или право. Обновите доску перед новой операцией.');return;}
      if(!response.ok)throw Error('http');
      setPending(null);setMessage(item.success);setRevision(n=>n+1);
    }catch{setMessage('Ответ неизвестен. Повторите операцию с тем же ключом.');}
    finally{setBusy(false);}
  }

  function saveConfig(){
    if(!base||!workflow||busy||pending)return;
    const item:PendingMutation={url:base+'/workflow',
      body:{expectedRevision:workflow.version,requestKey:crypto.randomUUID(),config:{stages}},
      success:'Конфигурация этапов сохранена.'};
    setPending(item);void submitMutation(item);
  }

  function assign(item:Item){
    const assignee=assignees[item.leadId]||'';
    if(!uuid.test(assignee)||!base||busy||pending)return;
    const mutation:PendingMutation={url:base+'/leads/'+encodeURIComponent(item.leadId)+'/assignment',
      body:{assigneeId:assignee,requestKey:crypto.randomUUID()},
      success:'Ответственный назначен.'};
    setPending(mutation);void submitMutation(mutation);
  }

  function transition(item:Item){
    if(!base||busy||pending||!editLead)return;
    const body:{stageCode:StageCode;reasonCode:LossReason|null;appointmentId:string|null;requestKey:string}={
      stageCode:moveStage,reasonCode:moveStage==='LOST'?reason:null,
      appointmentId:moveStage==='BOOKED'?appointment:null,requestKey:crypto.randomUUID(),
    };
    if(moveStage==='BOOKED'&&!uuid.test(appointment))return;
    const mutation:PendingMutation={url:base+'/leads/'+encodeURIComponent(item.leadId)+'/stages',
      body,success:'Этап обращения обновлён.'};
    setPending(mutation);void submitMutation(mutation);
  }

  React.useEffect(()=>{if(revision>0)void load();},[revision]);
  const names:Record<StageCode,string>={NEW:'Новое',QUALIFIED:'Обработано',BOOKED:'Записан',LOST:'Отказ'};
  const availableTransitions=(item:Item)=>board?.columns.filter(c=>c.enabled&&c.allowedFrom.includes(item.stageCode))||[];
  return <section aria-label="Канбан обращений CRM (dev)">
    <h1>Канбан обращений CRM (dev)</h1>
    <p>Только синтетический закрытый контур. Карточки не содержат ФИО,
      телефонов, email, VIN или текста обращения. До 200 карточек; при
      превышении сервер отказывает целиком, без частичной доски.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      disabled={busy||pending!==null} onChange={e=>{setOrg(e.target.value);setBoard(null);setWorkflow(null);setStatus('idle');}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      disabled={busy||pending!==null} onChange={e=>{setLoc(e.target.value);setBoard(null);setWorkflow(null);setStatus('idle');}}/></label>
    <button type="button" disabled={!base||busy} onClick={()=>void load()}>Загрузить канбан</button>
    {status==='loading'&&<p role="status">Загрузка канбана…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён. Данные очищены.</p>}
    {status==='error'&&<p role="alert">Не удалось загрузить канбан или превышен лимит 200 обращений.</p>}
    {status==='ready'&&workflow&&board&&<>
      {workflow.canConfigure&&<><h2>Настройка этапов филиала</h2>
      <p>Менеджер может менять названия, порядок, доступность и разрешённые
        причины отказа. Бизнес-переходы и требования к записи остаются
        серверными и проверяются PostgreSQL.</p>
      {stages.slice().sort((a,b)=>a.position-b.position).map((stage)=>(
        <fieldset key={stage.code}>
          <legend>{stage.code}</legend>
          <label>Название <input aria-label={'Название '+stage.code}
            value={stage.label} disabled={busy}
            onChange={e=>setStages(items=>items.map(x=>x.code===stage.code?
              {...x,label:e.target.value}:x))}/></label>
          <label>Порядок <input aria-label={'Порядок '+stage.code} type="number" min="0" max="99"
            value={stage.position} disabled={busy}
            onChange={e=>setStages(items=>items.map(x=>x.code===stage.code?
              {...x,position:Number(e.target.value)}:x))}/></label>
          <label><input type="checkbox" aria-label={'Этап активен '+stage.code}
            checked={stage.enabled} disabled={busy}
            onChange={e=>setStages(items=>items.map(x=>x.code===stage.code?
              {...x,enabled:e.target.checked}:x))}/>Этап активен</label>
          {stage.code==='LOST'&&<div>{reasons.map(r=><label key={r}>
            <input type="checkbox" aria-label={'Причина '+r}
              checked={stage.lossReasons.includes(r)} disabled={busy}
              onChange={e=>setStages(items=>items.map(x=>x.code===stage.code?
                {...x,lossReasons:e.target.checked?[...x.lossReasons,r]:
                  x.lossReasons.filter(v=>v!==r)}:x))}/>{reasonNames[r]}
          </label>)}</div>}
        </fieldset>
      ))}
      <button type="button" disabled={busy||pending!==null} onClick={saveConfig}>Сохранить конфигурацию этапов</button>
      <p>Версия конфигурации: {workflow.version}. NEW — системный начальный этап.</p></>}
      <h2>Доска филиала</h2>
      <div className="crm-kanban">
        {board.columns.map(column=><section key={column.code} aria-label={'Этап '+column.code}>
          <h3>{column.label} ({board.items.filter(i=>i.stageCode===column.code).length})</h3>
          {board.items.filter(i=>i.stageCode===column.code).map(item=><article key={item.leadId}>
            <p>Обращение: {item.leadId}</p>
            <p>Ответственный: {item.ownerId}</p>
            <p>Источник: {sourceNames[item.sourceCode]}</p>
            <label>Новый ответственный UUID <input aria-label={'Ответственный '+item.leadId}
              value={assignees[item.leadId]||''} disabled={busy}
              onChange={e=>setAssignees(x=>({...x,[item.leadId]:e.target.value}))}/></label>
            <button type="button" disabled={busy||pending!==null||!uuid.test(assignees[item.leadId]||'')}
              onClick={()=>void assign(item)}>Назначить</button>
            {!['BOOKED','LOST'].includes(item.stageCode)&&<button type="button"
              disabled={busy||pending!==null||availableTransitions(item).length===0}
              onClick={()=>{
                const allowed=availableTransitions(item);
                if(!allowed.length){setMessage('Для обращения нет доступных переходов.');return;}
                setEditLead(item.leadId);setMoveStage(allowed[0].code);
                setAppointment('');
                const loss=allowed.find(c=>c.code==='LOST');
                if(loss?.lossReasons.length)setReason(loss.lossReasons[0]);
              }}>
              Изменить этап</button>}
            {!['BOOKED','LOST'].includes(item.stageCode)&&availableTransitions(item).length===0&&
              <p>Нет доступных переходов.</p>}
            {editLead===item.leadId&&<div>
              <label>Этап <select aria-label={'Новый этап '+item.leadId}
                value={moveStage} onChange={e=>setMoveStage(e.target.value as StageCode)}>
                {availableTransitions(item).map(c=><option key={c.code} value={c.code}>{c.label}</option>)}
              </select></label>
              {moveStage==='BOOKED'&&<label>UUID записи <input aria-label={'UUID записи '+item.leadId}
                value={appointment} onChange={e=>setAppointment(e.target.value)}/></label>}
              {moveStage==='LOST'&&<label>Причина <select aria-label={'Причина отказа '+item.leadId}
                value={reason} onChange={e=>setReason(e.target.value as LossReason)}>
                {(board.columns.find(c=>c.code==='LOST')?.lossReasons||[]).map(r=><option key={r} value={r}>{reasonNames[r]}</option>)}
              </select></label>}
              <button type="button" disabled={busy||pending!==null||
                (moveStage==='BOOKED'&&!uuid.test(appointment))||
                (moveStage==='LOST'&&!(board.columns.find(c=>c.code==='LOST')?.lossReasons||[]).includes(reason))}
                onClick={()=>void transition(item)}>Сохранить этап</button>
              <button type="button" disabled={busy||pending!==null} onClick={()=>setEditLead('')}>Отмена</button>
            </div>}
          </article>)}
          {board.items.filter(i=>i.stageCode===column.code).length===0&&<p>Нет обращений.</p>}
        </section>)}
      </div>
    </>}
    {pending&&<button type="button" disabled={busy}
      onClick={()=>void submitMutation(pending)}>Повторить операцию с тем же ключом</button>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
