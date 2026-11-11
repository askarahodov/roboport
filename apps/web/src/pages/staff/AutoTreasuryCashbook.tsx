import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const day=/^\d{4}-\d{2}-\d{2}$/;
const minor=/^(0|[1-9]\d{0,17})$/;
const today=()=>new Date().toISOString().slice(0,10);
const money=(value:string)=>{
  const cents=BigInt(value);
  const rubles=cents/100n;
  const fraction=String(cents%100n).padStart(2,'0');
  return rubles.toLocaleString('ru-RU')+','+fraction;
};
type Kind='ADVANCE'|'PAYMENT'|'APPLY_ADVANCE'|'REFUND_ADVANCE'|'REFUND_PAYMENT';
type Flow='IN'|'OUT'|'INTERNAL';
type Event={eventId:string;kind:Kind;amountMinor:string;cashFlow:Flow;createdAt:string};
type Day={localDay:string;openingMinor:string;incomingMinor:string;
  outgoingMinor:string;internalAppliedMinor:string;closingMinor:string;
  closeId:string|null;events:Event[]};
type Summary={openingMinor:string;incomingMinor:string;outgoingMinor:string;
  internalAppliedMinor:string;closingMinor:string;eventCount:number;sealedDayCount:number};
type Book={syntheticOnly:true;realMoney:false;releaseAllowed:false;
  currency:'RUB';basis:'IMMUTABLE_TREASURY_ACCOUNT_CASHBOOK_DAYS';
  accountId:string;accountCode:string;accountKind:'CASH_SIM'|'BANK_SIM';
  fromDay:string;toDay:string;summary:Summary;items:Day[]};

function parse(value:unknown,from:string,to:string,account:string):Book{
  if(!value||typeof value!=='object')throw Error('contract');
  const book=value as Partial<Book>;
  if(book.syntheticOnly!==true||book.realMoney!==false||
    book.releaseAllowed!==false||book.currency!=='RUB'||
    book.basis!=='IMMUTABLE_TREASURY_ACCOUNT_CASHBOOK_DAYS'||
    book.accountId?.toLowerCase()!==account.toLowerCase()||
    typeof book.accountCode!=='string'||!/^[A-Z0-9_-]{2,32}$/.test(book.accountCode)||
    !['CASH_SIM','BANK_SIM'].includes(book.accountKind||'')||
    book.fromDay!==from||book.toDay!==to||
    !book.summary||!Array.isArray(book.items)||
    book.items.length<1||book.items.length>31)throw Error('contract');
  const s=book.summary;
  const numbers=[s.openingMinor,s.incomingMinor,s.outgoingMinor,
    s.internalAppliedMinor,s.closingMinor];
  if(!numbers.every(x=>typeof x==='string'&&minor.test(x))||
    !Number.isSafeInteger(s.eventCount)||s.eventCount<0||s.eventCount>1000||
    !Number.isSafeInteger(s.sealedDayCount)||s.sealedDayCount<0)throw Error('contract');
  const eventIds=new Set<string>();
  let current=BigInt(s.openingMinor);
  let paid=0n,refunded=0n,applied=0n,events=0,closed=0;
  const expectedDays=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  if(expectedDays!==book.items.length)throw Error('range');
  for(let index=0;index<book.items.length;index++){
    const row=book.items[index];
    const expected=new Date(Date.parse(from+'T00:00:00Z')+index*86400000)
      .toISOString().slice(0,10);
    if(!row||row.localDay!==expected||!day.test(row.localDay)||
      ![row.openingMinor,row.incomingMinor,row.outgoingMinor,
        row.internalAppliedMinor,row.closingMinor]
        .every(x=>typeof x==='string'&&minor.test(x))||
      !(row.closeId===null||typeof row.closeId==='string'&&uuid.test(row.closeId))||
      !Array.isArray(row.events)||row.events.length>1000||
      BigInt(row.openingMinor)!==current)throw Error('daily data');
    let dayIn=0n,dayOut=0n,dayApplied=0n;
    for(const e of row.events){
      if(!e||!uuid.test(e.eventId)||eventIds.has(e.eventId)||
        typeof e.amountMinor!=='string'||!minor.test(e.amountMinor)||
        BigInt(e.amountMinor)<1n||
        typeof e.createdAt!=='string'||e.createdAt.length>50)throw Error('event');
      eventIds.add(e.eventId);
      const amount=BigInt(e.amountMinor);
      const flow=e.kind==='ADVANCE'||e.kind==='PAYMENT'?'IN':
        e.kind==='REFUND_ADVANCE'||e.kind==='REFUND_PAYMENT'?'OUT':
        e.kind==='APPLY_ADVANCE'?'INTERNAL':null;
      if(flow!==e.cashFlow)throw Error('flow');
      if(flow==='IN')dayIn+=amount;
      else if(flow==='OUT')dayOut+=amount;
      else dayApplied+=amount;
      events++;
    }
    current=current+dayIn-dayOut;
    if(current<0n||current>1000000000000000n||
      current!==BigInt(row.closingMinor)||
      dayIn!==BigInt(row.incomingMinor)||
      dayOut!==BigInt(row.outgoingMinor)||
      dayApplied!==BigInt(row.internalAppliedMinor))throw Error('balance');
    paid+=dayIn;refunded+=dayOut;applied+=dayApplied;
    if(row.closeId!==null)closed++;
  }
  if(current!==BigInt(s.closingMinor)||paid!==BigInt(s.incomingMinor)||
    refunded!==BigInt(s.outgoingMinor)||applied!==BigInt(s.internalAppliedMinor)||
    events!==s.eventCount||closed!==s.sealedDayCount)throw Error('summary');
  return book as Book;
}
const names:Record<Kind,string>={
  ADVANCE:'Аванс',PAYMENT:'Оплата (SIM)',
  APPLY_ADVANCE:'Зачёт аванса без движения',
  REFUND_ADVANCE:'Возврат аванса',REFUND_PAYMENT:'Возврат оплаты',
};
const flows:Record<Flow,string>={IN:'Приход',OUT:'Расход',INTERNAL:'Без движения'};

export function AutoTreasuryCashbook(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [account,setAccount]=React.useState('');
  const [from,setFrom]=React.useState(today);
  const [to,setTo]=React.useState(today);
  const [status,setStatus]=React.useState<'idle'|'loading'|'ready'|'denied'|'invalid'|'limit'|'error'>('idle');
  const [book,setBook]=React.useState<Book|null>(null);
  const [exporting,setExporting]=React.useState(false);
  const [exportError,setExportError]=React.useState('');
  const request=React.useRef<AbortController|null>(null);
  const download=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>{request.current?.abort();download.current?.abort();},[]);
  const reset=()=>{
    request.current?.abort();download.current?.abort();
    setStatus('idle');setBook(null);setExporting(false);setExportError('');
  };
  const days=(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1;
  const valid=uuid.test(org)&&uuid.test(loc)&&uuid.test(account)&&
    day.test(from)&&day.test(to)&&Number.isInteger(days)&&days>=1&&days<=31;
  const path=()=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/treasury/accounts/'+encodeURIComponent(account)+
    '/cashbook';
  const query=()=>'?'+new URLSearchParams({fromDay:from,toDay:to});
  async function load(){
    if(!valid)return;
    request.current?.abort();download.current?.abort();
    setExporting(false);
    const pending=new AbortController();request.current=pending;
    setBook(null);setStatus('loading');setExportError('');
    try{
      const response=await fetch(path()+query(),{
        credentials:'include',cache:'no-store',signal:pending.signal,
      });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){setStatus('denied');return;}
      if(response.status===422){setStatus('invalid');return;}
      if(response.status===409){setStatus('limit');return;}
      if(!response.ok)throw Error('http');
      const body=parse(await response.json(),from,to,account);
      if(!pending.signal.aborted){setBook(body);setStatus('ready');}
    }catch{if(!pending.signal.aborted)setStatus('error');}
  }
  async function exportCsv(){
    if(!book||status!=='ready'||exporting)return;
    download.current?.abort();
    const pending=new AbortController();download.current=pending;
    setExporting(true);setExportError('');
    try{
      const response=await fetch(path()+'/export'+query(),{
        credentials:'include',cache:'no-store',signal:pending.signal,
      });
      if(pending.signal.aborted)return;
      if([401,403,404].includes(response.status)){
        setBook(null);setStatus('denied');return;
      }
      if(response.status===422){setBook(null);setStatus('invalid');return;}
      if(response.status===409){setBook(null);setStatus('limit');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/csv'))
        throw Error('export');
      const blob=await response.blob();
      if(pending.signal.aborted)return;
      const ref=URL.createObjectURL(blob);
      const link=document.createElement('a');
      link.href=ref;link.download='auto-treasury-cashbook.csv';
      document.body.appendChild(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(ref),1000);
    }catch{if(!pending.signal.aborted)setExportError('CSV не загружен. Повторите запрос.');}
    finally{if(!pending.signal.aborted)setExporting(false);}
  }
  return <section aria-label="Синтетическая кассовая книга (dev)">
    <h1>Кассовая книга: первичные движения и закрытие дня</h1>
    <p><strong>Только синтетический dev — не реальные деньги, не кассовый
      чек и не бухгалтерская проводка.</strong> Сверяются события одного
      условного счёта, остатки и неизменяемое техническое закрытие дня.
      Зачёт аванса не создаёт нового прихода. В книге нет имён клиентов,
      номеров автомобилей, комментариев и цен закупки.</p>
    <label>UUID организации <input aria-label="UUID организации" value={org}
      onChange={e=>{reset();setOrg(e.target.value);}}/></label>
    <label>UUID филиала <input aria-label="UUID филиала" value={loc}
      onChange={e=>{reset();setLoc(e.target.value);}}/></label>
    <label>UUID тестовой кассы или счёта <input aria-label="UUID тестового счёта"
      value={account} onChange={e=>{reset();setAccount(e.target.value);}}/></label>
    <label>Начало периода <input type="date" aria-label="Начало периода"
      value={from} onChange={e=>{reset();setFrom(e.target.value);}}/></label>
    <label>Конец периода <input type="date" aria-label="Конец периода"
      value={to} onChange={e=>{reset();setTo(e.target.value);}}/></label>
    <button type="button" disabled={!valid||status==='loading'}
      onClick={()=>void load()}>Сверить кассовую книгу</button>
    {!valid&&<p>Укажите три UUID и диапазон 1–31 день.</p>}
    {status==='loading'&&<p role="status">Проверяем финансовые записи в PostgreSQL…</p>}
    {status==='denied'&&<p role="alert">Доступ запрещён: проверьте роль, организацию, филиал и политику FINANCE.</p>}
    {status==='invalid'&&<p role="alert">Неверная дата, диапазон или UUID.</p>}
    {status==='limit'&&<p role="alert">Книга превышает 1000 событий или нарушена сверка. Отчёт не выдан.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить журнал. Повторите запрос.</p>}
    {exportError&&<p role="alert">{exportError}</p>}
    {status==='ready'&&book&&<section aria-label="Результаты кассовой книги">
      <p role="status">Счёт {book.accountCode}. Событий: {book.summary.eventCount}.
        Дней закрыто: {book.summary.sealedDayCount}.
        Конечный остаток: {money(book.summary.closingMinor)} RUB (SIM).</p>
      <p>Начальный остаток: {money(book.summary.openingMinor)};
        приход: {money(book.summary.incomingMinor)};
        расход: {money(book.summary.outgoingMinor)};
        зачёт авансов без движения: {money(book.summary.internalAppliedMinor)}.
        Выгрузка повторно проверяет права и формирует новый срез журнала.</p>
      <button type="button" onClick={()=>void exportCsv()} disabled={exporting}>
        {exporting?'Готовим CSV…':'Скачать полную кассовую книгу CSV'}
      </button>
      <div style={{overflowX:'auto'}}>
        <table>
          <caption>Сверка тестовых движений по дням</caption>
          <thead><tr><th scope="col">Локальный день</th>
            <th scope="col">Начало, SIM RUB</th><th scope="col">Приход</th>
            <th scope="col">Расход</th><th scope="col">Зачёт без движения</th>
            <th scope="col">Конец, SIM RUB</th><th scope="col">Закрытие</th>
            <th scope="col">Первичные события</th></tr></thead>
          <tbody>{book.items.map(d=><tr key={d.localDay}>
            <th scope="row">{d.localDay}</th>
            <td>{money(d.openingMinor)}</td><td>{money(d.incomingMinor)}</td>
            <td>{money(d.outgoingMinor)}</td><td>{money(d.internalAppliedMinor)}</td>
            <td>{money(d.closingMinor)}</td>
            <td>{d.closeId?'Закрыто':'Не закрыто'}</td>
            <td><details><summary>Событий: {d.events.length}</summary>
              {d.events.length===0?<p>За день движений нет.</p>:
                <ul>{d.events.map(e=><li key={e.eventId}>
                  {names[e.kind]} — {flows[e.cashFlow]} {money(e.amountMinor)}
                  {' · '}<code>{e.eventId}</code>
                </li>)}</ul>}
            </details></td>
          </tr>)}</tbody>
        </table>
      </div>
    </section>}
  </section>;
}
