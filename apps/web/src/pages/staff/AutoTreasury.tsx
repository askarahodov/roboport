import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const digestDay=/^\d{4}-\d{2}-\d{2}$/;
type Account={id:string;code:string;label:string;kind:'CASH_SIM'|'BANK_SIM';
  currency:'RUB';balanceMinor:number;lastClosedDay:string|null};
type Accounts={syntheticOnly:true;realMoney:false;currency:'RUB';items:Account[]};
type Movement={id:string;accountId:string;revisionId:string;kind:string;
  amountMinor:number;reason:string;businessDay:string;createdAt:string};
type Order={syntheticOnly:true;realMoney:false;releaseAllowed:false;orderId:string;
  currency:'RUB';approvedRevisionId:string|null;approvedTotalMinor:number|null;
  advancedMinor:number;paymentMinor:number;appliedMinor:number;
  refundedAdvanceMinor:number;refundedPaymentMinor:number;
  unappliedAdvanceMinor:number;settledMinor:number;cashNetMinor:number;
  dueMinor:number|null;events:Movement[]};
type Day={accountId:string;accountCode:string;kind:string;localDay:string;
  openingMinor:number;incomingMinor:number;outgoingMinor:number;
  closingMinor:number;closeId:string|null;closedAt:string|null};
type Report={syntheticOnly:true;realMoney:false;currency:'RUB';
  basis:'INDEPENDENT_TREASURY_EVENT_DAYS';accounts:Account[];items:Day[]};
type Pending={url:string;body:string};
const isAmount=(v:unknown):v is number=>
  Number.isSafeInteger(v)&&typeof v==='number'&&v>=0&&v<=1e15;
function parseAccounts(v:unknown):Accounts{
  const x=v as Accounts;
  if(!x||x.syntheticOnly!==true||x.realMoney!==false||x.currency!=='RUB'||
    !Array.isArray(x.items)||x.items.length>50||!x.items.every(a=>
      a&&uuid.test(a.id)&&typeof a.code==='string'&&typeof a.label==='string'&&
      ['CASH_SIM','BANK_SIM'].includes(a.kind)&&a.currency==='RUB'&&
      isAmount(a.balanceMinor)&&(a.lastClosedDay===null||
        digestDay.test(a.lastClosedDay))))throw Error('Invalid accounts');
  return x;
}
function parseOrder(v:unknown):Order{
  const x=v as Order;
  if(!x||x.syntheticOnly!==true||x.realMoney!==false||
    x.releaseAllowed!==false||x.currency!=='RUB'||
    !Array.isArray(x.events)||x.events.length>100||
    ![x.advancedMinor,x.paymentMinor,x.appliedMinor,x.refundedAdvanceMinor,
      x.refundedPaymentMinor,x.unappliedAdvanceMinor,x.settledMinor,
      x.cashNetMinor].every(isAmount)||
    (x.approvedTotalMinor!==null&&!isAmount(x.approvedTotalMinor))||
    (x.approvedTotalMinor===null?x.dueMinor!==null:
      x.dueMinor!==x.approvedTotalMinor-x.settledMinor)||
    x.unappliedAdvanceMinor!==x.advancedMinor-x.appliedMinor-x.refundedAdvanceMinor||
    x.settledMinor!==x.paymentMinor+x.appliedMinor-x.refundedPaymentMinor||
    x.cashNetMinor!==x.advancedMinor+x.paymentMinor-
      x.refundedAdvanceMinor-x.refundedPaymentMinor||
    (x.approvedTotalMinor!==null&&
      x.unappliedAdvanceMinor+x.settledMinor>x.approvedTotalMinor)||
    !x.events.every(e=>uuid.test(e.id)&&uuid.test(e.accountId)&&
      uuid.test(e.revisionId)&&isAmount(e.amountMinor)&&
      digestDay.test(e.businessDay)))throw Error('Invalid treasury order');
  return x;
}
function parseReport(v:unknown):Report{
  const x=v as Report;
  if(!x||x.syntheticOnly!==true||x.realMoney!==false||x.currency!=='RUB'||
    x.basis!=='INDEPENDENT_TREASURY_EVENT_DAYS'||
    !Array.isArray(x.items)||x.items.length>1550||
    !Array.isArray(x.accounts)||
    !x.items.every(day=>uuid.test(day.accountId)&&digestDay.test(day.localDay)&&
       [day.openingMinor,day.incomingMinor,day.outgoingMinor,day.closingMinor].every(isAmount)&&
       day.closingMinor===day.openingMinor+day.incomingMinor-day.outgoingMinor&&
       (day.closeId===null||uuid.test(day.closeId))))throw Error('Invalid treasury report');
  return x;
}
const money=(x:number)=>(x/100).toLocaleString('ru-RU',{
  minimumFractionDigits:2,maximumFractionDigits:2,
});
const today=()=>new Date().toISOString().slice(0,10);
const actions=['ADVANCE','PAYMENT','APPLY_ADVANCE','REFUND_ADVANCE','REFUND_PAYMENT'] as const;
const names:Record<typeof actions[number],string>={
  ADVANCE:'Принять тестовый аванс',
  PAYMENT:'Условная оплата работ после QC',
  APPLY_ADVANCE:'Зачесть ранее внесённый аванс (без движения)',
  REFUND_ADVANCE:'Условный возврат незачтённого аванса',
  REFUND_PAYMENT:'Условный возврат проведённой оплаты',
};

export function AutoTreasury(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [order,setOrder]=React.useState('');
  const [account,setAccount]=React.useState('');
  const [code,setCode]=React.useState('CASH-01');
  const [label,setLabel]=React.useState('Тестовая касса');
  const [kind,setKind]=React.useState<'CASH_SIM'|'BANK_SIM'>('CASH_SIM');
  const [movement,setMovement]=React.useState<typeof actions[number]>('ADVANCE');
  const [amount,setAmount]=React.useState('');
  const [reason,setReason]=React.useState('Тестовая операция');
  const [fromDay,setFromDay]=React.useState(today),[toDay,setToDay]=React.useState(today);
  const [closeDay,setCloseDay]=React.useState(today);
  const [accounts,setAccounts]=React.useState<Accounts|null>(null);
  const [ledger,setLedger]=React.useState<Order|null>(null);
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'error'>('idle');
  const [busy,setBusy]=React.useState(false);
  const [revision,setRevision]=React.useState(0);
  const [message,setMessage]=React.useState('');
  const [pending,setPending]=React.useState<Pending|null>(null);
  const scope=org.trim()+':'+loc.trim()+':'+order.trim();
  const valid=uuid.test(org.trim())&&uuid.test(loc.trim());
  const base='/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
    '/locations/'+encodeURIComponent(loc.trim())+'/treasury';
  const validOrder=uuid.test(order.trim());
  const dateRange=digestDay.test(fromDay)&&digestDay.test(toDay)&&
    (Date.parse(toDay+'T00:00:00Z')-Date.parse(fromDay+'T00:00:00Z'))/86400000>=0&&
    (Date.parse(toDay+'T00:00:00Z')-Date.parse(fromDay+'T00:00:00Z'))/86400000<31;
  const num=Number(amount);
  const validAmount=amount.trim()!==''&&Number.isSafeInteger(num)&&num>=1&&num<=1e12;
  React.useEffect(()=>{
    setAccounts(null);setLedger(null);setReport(null);setMessage('');setPending(null);
    if(!valid){setStatus('idle');return;}
    const controller=new AbortController();
    setStatus('loading');
    (async()=>{
      try{
        const req=(url:string)=>fetch(url,{credentials:'include',cache:'no-store',
          signal:controller.signal});
        const a=await req(base+'/accounts');
        if(controller.signal.aborted)return;
        if([401,403,404].includes(a.status)){setStatus('denied');return;}
        if(!a.ok)throw Error('accounts');
        const data=parseAccounts(await a.json());
        let orderData:Order|null=null;
        if(validOrder){
          const b=await req(base+'/orders/'+encodeURIComponent(order.trim()));
          if([401,403,404].includes(b.status)){setStatus('denied');return;}
          if(!b.ok)throw Error('order');
          orderData=parseOrder(await b.json());
        }
        if(controller.signal.aborted)return;
        setAccounts(data);setLedger(orderData);setStatus('ready');
      }catch{if(!controller.signal.aborted)setStatus('error');}
    })();
    return ()=>controller.abort();
  },[scope,valid,validOrder,revision]);

  async function submit(url:string,body:string){
    if(busy||status!=='ready')return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',{
        method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('CSRF');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
        typeof token.csrfToken!=='string')throw Error('CSRF');
      const result=await fetch(url,{method:'POST',credentials:'include',
        cache:'no-store',headers:{'Content-Type':'application/json',
          'X-CSRF-Token':token.csrfToken},body});
      if([401,403,404].includes(result.status)){
        setAccounts(null);setLedger(null);setReport(null);
        setStatus('denied');setPending(null);return;
      }
      if(result.status===409||result.status===422){
        setPending(null);
        setMessage(result.status===409?'409 — операция отклонена: лимит, QC, закрытый день или повтор другого ключа. Обновите данные.':
          '422 — проверьте введённые поля, UUID, даты и сумму.');
        setRevision(n=>n+1);return;
      }
      if(!result.ok)throw Error('network');
      const payload:unknown=await result.json();
      if(!payload||typeof payload!=='object'||!('syntheticOnly' in payload)||
        payload.syntheticOnly!==true)throw Error('contract');
      setPending(null);
      setMessage('Техническая запись принята. Журнал будет перечитан из PostgreSQL.');
      setRevision(n=>n+1);
    }catch{
      setPending({url,body});
      setMessage('Результат неизвестен. Повторите тот же запрос с прежним requestKey, а не создавайте дубликат.');
    }finally{setBusy(false);}
  }
  function action(path:string,fields:Record<string,unknown>){
    if(!valid||status!=='ready'||busy||pending)return;
    void submit(base+path,JSON.stringify({...fields,requestKey:crypto.randomUUID()}));
  }
  async function loadReport(){
    if(!valid||!dateRange||status!=='ready')return;
    setReport(null);setMessage('');
    try{
      const url=base+'/report?'+new URLSearchParams({
        fromDay,toDay,
      });
      const response=await fetch(url,{credentials:'include',cache:'no-store'});
      if([401,403,404].includes(response.status)){
        setStatus('denied');setAccounts(null);setLedger(null);return;
      }
      if(!response.ok)throw Error('Report');
      setReport(parseReport(await response.json()));
    }catch{setMessage('Сверка не получена. Проверьте период и права и повторите.');}
  }
  const ready=status==='ready'&&accounts!==null&&!busy&&!pending;
  return <section aria-label="Синтетическая касса и сверка" className="ui-page"
    style={{maxWidth:'77rem',margin:'1rem auto',overflowWrap:'anywhere'}}>
    <h1>Кассы, авансы и возвраты — DEV</h1>
    <p><strong>Исключительно синтетический учёт.</strong> Нет денег, банковских callback,
      фискальных чеков, кассовой техники и юридических подтверждений. Этот
      реестр не равен прежним CHARGE/CREDIT и SIM_PAYMENT/SIM_REFUND и никогда
      не суммируется с ними.</p>
    <div style={{display:'flex',flexWrap:'wrap',gap:'0.75rem'}}>
      <label>UUID организации <input aria-label="UUID организации" value={org}
        onChange={e=>setOrg(e.target.value)}/></label>
      <label>UUID филиала <input aria-label="UUID филиала" value={loc}
        onChange={e=>setLoc(e.target.value)}/></label>
      <label>UUID заказ-наряда <input aria-label="UUID заказ-наряда" value={order}
        onChange={e=>setOrder(e.target.value)}/></label>
    </div>
    {status==='idle'&&<p>Укажите организацию и филиал. Заказ нужен для движений.</p>}
    {status==='loading'&&<p role="status">Загрузка синтетического реестра…</p>}
    {status==='denied'&&<p role="alert">403 — доступ запрещён или отозвана роль/связь.</p>}
    {status==='error'&&<p role="alert">Не удалось загрузить реестр.
      <button type="button" onClick={()=>setRevision(x=>x+1)}>Повторить загрузку</button></p>}
    {accounts&&status==='ready'&&<>
      <h2>Счета филиала (RUB, тестовые копейки)</h2>
      {accounts.items.length===0?<p>Тестовых касс пока нет.</p>:
        <ul>{accounts.items.map(a=><li key={a.id}>
          <strong>{a.code} · {a.label}</strong> ({a.kind}), остаток:
          {' '}{money(a.balanceMinor)} RUB, последний закрытый день:
          {' '}{a.lastClosedDay??'нет'}
        </li>)}</ul>}
      <form onSubmit={e=>{e.preventDefault();action('/accounts',{code,label,kind});}}>
        <h3>Создать тестовую кассу / счёт (MANAGER)</h3>
        <label>Код счёта <input aria-label="Код счёта" value={code}
          onChange={e=>setCode(e.target.value)}/></label>
        <label>Название <input aria-label="Название счёта" value={label}
          onChange={e=>setLabel(e.target.value)}/></label>
        <label>Тип <select aria-label="Тип счёта" value={kind}
          onChange={e=>setKind(e.target.value as 'CASH_SIM'|'BANK_SIM')}>
          <option value="CASH_SIM">Условная касса</option>
          <option value="BANK_SIM">Условный расчётный счёт</option>
        </select></label>
        <button type="submit" disabled={!ready||!(/^[A-Z0-9_-]{2,32}$/).test(code)||
          label.trim().length<3||label.trim().length>80}>
          Создать тестовый счёт</button>
      </form>
      <label>Счёт для операций <select aria-label="Счёт для операций" value={account}
        onChange={e=>setAccount(e.target.value)}>
        <option value="">Выберите счёт</option>
        {accounts.items.map(a=><option value={a.id} key={a.id}>{a.code} · {a.label}</option>)}
      </select></label>
      {ledger&&<>
        <h2>Сверка заказ-наряда</h2>
        <p>Согласовано: {ledger.approvedTotalMinor===null?'нет':
          money(ledger.approvedTotalMinor)+' RUB'}.
          {' '}Внесённые авансы: {money(ledger.advancedMinor)}.
          {' '}Незачтённый аванс: {money(ledger.unappliedAdvanceMinor)}.
          {' '}Зачтено и оплачено: {money(ledger.settledMinor)}.
          {' '}Остаток долга: {ledger.dueMinor===null?'нет':
            money(ledger.dueMinor)+' RUB'}.</p>
        <h3>Неизменяемая история по заказу</h3>
        {ledger.events.length===0?<p>Движений пока нет.</p>:
          <ol>{ledger.events.map(e=><li key={e.id}>
            {e.businessDay} · {e.kind} · {money(e.amountMinor)} RUB ·
            {' '}{e.reason} · <code>{e.id}</code>
          </li>)}</ol>}
        <form onSubmit={e=>{e.preventDefault();action('/orders/'+encodeURIComponent(order.trim())+
          '/events',{accountId:account,kind:movement,amountMinor:num,reason});}}>
          <h3>Записать синтетическую операцию (CASHIER)</h3>
          <label>Операция <select aria-label="Операция" value={movement}
            onChange={e=>setMovement(e.target.value as typeof movement)}>
            {actions.map(x=><option value={x} key={x}>{names[x]}</option>)}
          </select></label>
          <label>Сумма, коп. <input aria-label="Сумма, коп." type="number"
            min="1" max="1000000000000" step="1" value={amount}
            onChange={e=>setAmount(e.target.value)}/></label>
          <label>Основание <input aria-label="Основание" value={reason}
            onChange={e=>setReason(e.target.value)}/></label>
          <button type="submit" disabled={!ready||!uuid.test(account)||!validOrder||
            !validAmount||reason.trim().length<3||reason.trim().length>160}>
            Записать движение (не реальные деньги)</button>
        </form>
      </>}
      <form onSubmit={e=>{e.preventDefault();action('/accounts/'+encodeURIComponent(account)+
        '/close-day',{businessDay:closeDay});}}>
        <h3>Закрыть синтетический день (CASHIER)</h3>
        <p>Перед закрытием убедитесь, что все события дня внесены.
          После закрытия задним числом запись невозможна.</p>
        <label>Локальный день <input aria-label="Локальный день" type="date"
          value={closeDay} onChange={e=>setCloseDay(e.target.value)}/></label>
        <button type="submit" disabled={!ready||!uuid.test(account)||!digestDay.test(closeDay)}>
          Зафиксировать закрытие дня</button>
      </form>
      <h2>Сверка движения денежных средств — только модель</h2>
      <div style={{display:'flex',gap:'0.75rem',flexWrap:'wrap'}}>
        <label>Начало периода <input type="date" aria-label="Начало периода"
          value={fromDay} onChange={e=>setFromDay(e.target.value)}/></label>
        <label>Конец периода <input type="date" aria-label="Конец периода"
          value={toDay} onChange={e=>setToDay(e.target.value)}/></label>
        <button type="button" disabled={!ready||!dateRange} onClick={()=>void loadReport()}>
          Сверить модельные остатки</button>
      </div>
      {report&&<div style={{overflowX:'auto'}}>
        <table>
          <caption>Счёт · локальная дата · остаток начало + приход − расход = конец</caption>
          <thead><tr>{['Счёт','День','Начало, коп.','Приход, коп.','Расход, коп.',
            'Конец, коп.','Закрытие'].map(h=><th scope="col" key={h}>{h}</th>)}</tr></thead>
          <tbody>{report.items.map(row=><tr key={row.accountId+row.localDay}>
            <td>{row.accountCode}</td><td>{row.localDay}</td>
            <td>{row.openingMinor}</td><td>{row.incomingMinor}</td>
            <td>{row.outgoingMinor}</td><td>{row.closingMinor}</td>
            <td>{row.closeId?'Зафиксирован':'Открыт'}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      <p role="note">Фактическая оплата, платежи СБП/банк, ККТ, 54-ФЗ и ЭДО недоступны.
        Любой возврат здесь — лишь синтетическая запись.</p>
      <button type="button" disabled={busy} onClick={()=>setRevision(x=>x+1)}>
        Обновить журнал</button>
    </>}
    {message&&<p role="alert">{message}</p>}
    {pending&&status==='ready'&&<div role="group" aria-label="Повтор неизвестного запроса">
      <button type="button" disabled={busy}
        onClick={()=>void submit(pending.url,pending.body)}>Повторить тот же запрос</button>
      <button type="button" disabled={busy} onClick={()=>{
        setPending(null);setRevision(x=>x+1);}}>Перечитать журнал</button>
    </div>}
  </section>;
}
