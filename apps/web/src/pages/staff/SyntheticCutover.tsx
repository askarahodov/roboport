import React from 'react';

const names=['vehicles.csv','services.csv','inventory.csv'] as const;
type Name=typeof names[number];
type Files=Partial<Record<Name,File>>;
type Counts={vehicles:number;services:number;stockRows:number;skus:number;receipts:number};
type Receipt={ok:true;syntheticOnly:true;productionApproved:false;bundleSha256:string;
  counts:Counts;batchIds:Record<Name,string>;mode:string;writes:number;replayed?:boolean};
type Operation='preview'|'commit'|'reconcile'|'export';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const hash=/^[0-9a-f]{64}$/;
const keys=['vehicles','services','stockRows','skus','receipts'] as const;
const labels=['Автомобили','Работы','Строки склада','Артикулы','Приёмки'];
const hex=(bytes:ArrayBuffer)=>Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,'0')).join('');
const digest=async(bytes:ArrayBuffer)=>hex(await crypto.subtle.digest('SHA-256',bytes));
function b64(bytes:ArrayBuffer){
  const view=new Uint8Array(bytes);let text='';
  for(let i=0;i<view.length;i+=8192)text+=String.fromCharCode(...view.subarray(i,i+8192));
  return btoa(text);
}
function valid(raw:unknown,operation:Operation,prior:Receipt|null):raw is Receipt{
  if(!raw||typeof raw!=='object')return false;
  const x=raw as Receipt;
  return x.ok===true&&x.syntheticOnly===true&&x.productionApproved===false&&
    x.mode===operation.toUpperCase()&&typeof x.bundleSha256==='string'&&hash.test(x.bundleSha256)&&
    Number.isSafeInteger(x.writes)&&x.writes>=0&&
    (operation==='commit'||x.writes===0)&&
    (operation!=='commit'||(typeof x.replayed==='boolean'&&(!x.replayed||x.writes===0)))&&
    !!x.counts&&Object.keys(x.counts).length===keys.length&&
    keys.every(k=>Number.isSafeInteger(x.counts[k])&&x.counts[k]>=0&&x.counts[k]<=2000)&&
    x.counts.vehicles>0&&x.counts.services>0&&x.counts.skus>0&&
    x.counts.skus+x.counts.receipts<=x.counts.stockRows&&
    !!x.batchIds&&Object.keys(x.batchIds).length===3&&names.every(n=>uuid.test(x.batchIds[n]))&&
    new Set(names.map(n=>x.batchIds[n])).size===3&&
    (operation==='preview'||!!prior&&x.bundleSha256===prior.bundleSha256&&
      keys.every(k=>x.counts[k]===prior.counts[k])&&names.every(n=>x.batchIds[n]===prior.batchIds[n]));
}
export function SyntheticCutover(){
  const [org,setOrg]=React.useState(''),[loc,setLoc]=React.useState('');
  const [files,setFiles]=React.useState<Files>({});
  const [ack,setAck]=React.useState(false),[confirm,setConfirm]=React.useState(false);
  const [receipt,setReceipt]=React.useState<Receipt|null>(null);
  const [status,setStatus]=React.useState(''),[busy,setBusy]=React.useState(false);
  const [reconciled,setReconciled]=React.useState(false);
  const [manifest,setManifest]=React.useState('');
  const epoch=React.useRef(0),request=React.useRef<AbortController|null>(null),running=React.useRef(false);
  React.useEffect(()=>()=>{epoch.current++;request.current?.abort();},[]);
  function invalidate(clearFiles=false){
    epoch.current++;request.current?.abort();running.current=false;setBusy(false);
    setReceipt(null);setConfirm(false);setReconciled(false);setManifest('');setStatus('');
    if(clearFiles){setFiles({});setAck(false);}
  }
  function deny(){
    invalidate(true);setStatus('Доступ запрещён или тестовый импорт отключён. Данные очищены.');
  }
  const scope=uuid.test(org.trim())&&uuid.test(loc.trim());
  const ready=scope&&ack&&names.every(n=>!!files[n]&&files[n]!.size>0&&files[n]!.size<=1000000);
  function demo(){
    invalidate();const key=crypto.randomUUID().replaceAll('-','').toUpperCase();
    const o=org.trim().toLowerCase(),l=loc.trim().toLowerCase(),id=key.slice(0,16);
    const text:Record<Name,string>={
      'vehicles.csv':'record_type,source_id,organization_id,location_id,vin,make,model,source_system\n'+
        'VEHICLE,SYN-'+id+','+o+','+l+','+key.slice(0,17)+',SYNTHETIC,TEST CAR,DEMO\n',
      'services.csv':'record_type,source_system,source_id,code,title,duration_minutes\n'+
        'SERVICE,DEMO,SYN-'+id+',SYN-WORK-'+id+',SYNTHETIC SERVICE,60\n',
      'inventory.csv':'record_type,source_id,organization_id,location_id,article,quantity\n'+
        'SKU,SYN-SKU,'+o+','+l+',SYN-PART-'+id+',0\nSTOCK,SYN-STOCK,'+o+','+l+',SYN-PART-'+id+',3\n'};
    setFiles(Object.fromEntries(names.map(n=>[n,new File([text[n]],n,{type:'text/csv'})])) as Files);
    setStatus('Создан искусственный комплект. Проверьте его перед импортом.');
  }
  async function run(operation:Operation){
    if(running.current||!ready||(operation!=='preview'&&!receipt)||
      (operation==='commit'&&!confirm)||(operation==='export'&&!reconciled))return;
    running.current=true;setBusy(true);setManifest('');
    if(operation!=='export')setReconciled(false);
    const currentEpoch=++epoch.current,controller=new AbortController();
    request.current=controller;
    const current=()=>currentEpoch===epoch.current&&!controller.signal.aborted;
    setStatus('Выполняется проверка…');
    try{
      const entries=await Promise.all(names.map(async n=>{
        const bytes=await files[n]!.arrayBuffer();
        return [n,{base64:b64(bytes),sha256:await digest(bytes)}] as const;
      }));
      if(!current())return;
      const csrf=await fetch('/api/v1/auth/csrf',{method:'POST',credentials:'include',cache:'no-store',signal:controller.signal});
      if(!current())return;
      if([401,403,404].includes(csrf.status)){deny();return;}
      if(!csrf.ok)throw Error('csrf');
      const token=await csrf.json();
      if(typeof token.csrfToken!=='string'||!token.csrfToken)throw Error('csrf');
      if(!current())return;
      const response=await fetch('/api/v1/dev/auto/organizations/'+encodeURIComponent(org.trim())+
        '/locations/'+encodeURIComponent(loc.trim())+'/cutover/'+operation,{
        method:'POST',credentials:'include',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrfToken},
        body:JSON.stringify({syntheticOnly:true,files:Object.fromEntries(entries),
          ...(operation==='preview'?{}:{bundleSha256:receipt!.bundleSha256})})});
      if(!current())return;
      if([401,403,404].includes(response.status)){deny();return;}
      if(response.status===409||response.status===422){
        setConfirm(false);setReceipt(null);setReconciled(false);
        setStatus(response.status===409?'Конфликт импорта или сверки. Повторите предварительную проверку тех же файлов.':
          'Неверный формат, состав, контрольная сумма или филиал. Проверьте искусственные CSV.');return;
      }
      if(!response.ok)throw Error('response');
      if(operation==='export'){
        const expected=response.headers.get('X-Cutover-SHA256'),manifestHash=response.headers.get('X-Cutover-Manifest-SHA256');
        if(!expected||!hash.test(expected)||!manifestHash||!hash.test(manifestHash)||
          !response.headers.get('content-type')?.startsWith('application/zip'))throw Error('archive');
        const bytes=await response.arrayBuffer();
        if(bytes.byteLength>3100000||await digest(bytes)!==expected)throw Error('archive');
        if(!current())return;
        const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
        const link=document.createElement('a');link.href=url;link.download='synthetic-cutover.zip';
        document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);
        setManifest(manifestHash);setStatus('Архив сохранён после проверки SHA-256.');return;
      }
      const data:unknown=await response.json();
      if(!current())return;
      if(!valid(data,operation,receipt))throw Error('receipt');
      setReceipt(data);setConfirm(false);
      setReconciled(operation==='reconcile');
      setStatus(operation==='preview'?'Структура проверена. Записей в БД нет.':
        operation==='reconcile'?'Исходники, staging и активированные объекты сверены.':
        data.replayed?'Подтверждён повтор без новых записей.':'Импорт завершён атомарно. Выполните отдельную сверку.');
    }catch{
      if(current()){
        setConfirm(false);setReconciled(false);
        setStatus(operation==='commit'?'Результат неизвестен. Повторите предварительную проверку и тот же пакет, либо выполните сверку.':
          'Не удалось подтвердить результат. Повторите проверку; файл не сохранён.');
      }
    }finally{if(current()){running.current=false;setBusy(false);}}
  }
  return <section aria-label="Синтетический импорт" style={{maxWidth:'60rem',margin:'2rem auto',overflowWrap:'anywhere'}}>
    <h1>Импорт данных автосервиса (dev)</h1>
    <p>Только изолированный синтетический стенд. Реальные VIN, клиенты, история, документы и платежи запрещены.
      Предварительная проверка не разрешает production и не гарантирует отсутствие конфликтов в базе.</p>
    <label>UUID организации <input value={org} disabled={busy} onChange={e=>{invalidate(true);setOrg(e.target.value);}}/></label>{' '}
    <label>UUID филиала <input value={loc} disabled={busy} onChange={e=>{invalidate(true);setLoc(e.target.value);}}/></label>
    <p>Требуются живые роли MANAGER и WAREHOUSE одного филиала и отдельный флаг стенда.</p>
    <button type="button" disabled={!scope||busy} onClick={demo}>Создать искусственный комплект</button>
    <fieldset disabled={busy}><legend>Три исходных CSV (до 1 МБ каждый)</legend>
      {names.map(n=><div key={n}><label>{n} <input key={org+loc+n+(files[n]?.name??'')} type="file" accept=".csv,text/csv"
        onChange={e=>{const file=e.target.files?.[0];invalidate();setFiles(old=>({...old,[n]:file}));}}/></label>
        <p>{files[n]?files[n]!.name+' · '+files[n]!.size+' байт':'Файл не выбран'}</p></div>)}
      <label><input type="checkbox" checked={ack} onChange={e=>{invalidate();setAck(e.target.checked);}}/>
        Подтверждаю, что все файлы искусственные, без реальных данных</label>
    </fieldset>
    <button type="button" disabled={!ready||busy} onClick={()=>void run('preview')}>Предварительная проверка</button>
    {receipt&&<div><h2>Проверенный комплект</h2>
      <p>SHA-256: {receipt.bundleSha256}</p>
      <dl>{keys.map((k,i)=><React.Fragment key={k}><dt>{labels[i]}</dt><dd>{receipt.counts[k]}</dd></React.Fragment>)}</dl>
      <p>Записей в операции: {receipt.writes}. Production запрещён.</p>
      <label><input type="checkbox" checked={confirm} disabled={busy} onChange={e=>setConfirm(e.target.checked)}/>
        Подтверждаю атомарную запись этого комплекта в тестовую БД</label>
      <div><button type="button" disabled={busy||!confirm||!ready} onClick={()=>void run('commit')}>Импортировать комплект</button>{' '}
      <button type="button" disabled={busy||!ready} onClick={()=>void run('reconcile')}>Сверить с БД</button>{' '}
      <button type="button" disabled={busy||!ready||!reconciled} onClick={()=>void run('export')}>Скачать проверенный ZIP</button></div>
    </div>}
    {manifest&&<p>SHA-256 манифеста (сохраните отдельно): {manifest}</p>}
    {status&&<p role="status">{status}</p>}
  </section>;
}
