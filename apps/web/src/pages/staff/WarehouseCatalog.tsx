import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const articlePattern=/^[A-Z0-9][A-Z0-9._/-]{1,79}$/;
const categoryPattern=/^[A-Z0-9][A-Z0-9_-]{1,31}$/;
const integer=(x:string)=>/^(0|[1-9][0-9]{0,9})$/.test(x)&&Number(x)<=1e9;
type Item={
  skuId:string;article:string;category:string|null;uom:string|null;
  barcode:string|null;brand:string|null;version:number;
  purchaseMinor:number|null;saleMinor:number|null;
  purchaseHidden:boolean;saleHidden:boolean;mergedInto:string|null;
};
type Preview={previewId:string;sha256:string;items:Array<{
  article:string;skuId:string|null;version:number}>;replayed:boolean;syntheticOnly:true};
type Pending={kind:string;url:string;key:string;body:string|Uint8Array;mime:string;
  org:string;loc:string};
function validItem(x:unknown):x is Item{
  if(!x||typeof x!=='object')return false;
  const v=x as Item;
  return uuid.test(v.skuId)&&typeof v.article==='string'&&v.article.length>0&&
    Number.isSafeInteger(v.version)&&v.version>=0&&
    (v.purchaseMinor===null||Number.isSafeInteger(v.purchaseMinor))&&
    (v.saleMinor===null||Number.isSafeInteger(v.saleMinor))&&
    typeof v.purchaseHidden==='boolean'&&typeof v.saleHidden==='boolean'&&
    (v.mergedInto===null||uuid.test(v.mergedInto))&&
    (v.category===null||typeof v.category==='string')&&
    (v.uom===null||typeof v.uom==='string');
}
function reason(status:number){
  if(status===403||status===401||status===404)return '403 — нет активных прав на организацию, филиал, документ или цены.';
  if(status===409)return '409 — устаревшая версия, конфликт ключа, несовместимые SKU или незакрытый остаток.';
  if(status===422)return '422 — проверьте артикул, формат XLSX, цены, количество и UUID.';
  return 'Ошибка запроса к синтетическому серверу.';
}

export function WarehouseCatalog(){
  const [org,setOrg]=React.useState('');
  const [loc,setLoc]=React.useState('');
  const [rows,setRows]=React.useState<Item[]>([]);
  const [cursor,setCursor]=React.useState<string|null>(null);
  const [reading,setReading]=React.useState(false);
  const [listStatus,setListStatus]=React.useState('');
  const [message,setMessage]=React.useState('');
  const [busy,setBusy]=React.useState(false);
  const [pending,setPending]=React.useState<Pending|null>(null);
  const [article,setArticle]=React.useState('SYNTH-NEW-001');
  const [category,setCategory]=React.useState('PARTS');
  const [uom,setUom]=React.useState('PCS');
  const [barcode,setBarcode]=React.useState('');
  const [brand,setBrand]=React.useState('');
  const [purchase,setPurchase]=React.useState('1200');
  const [sale,setSale]=React.useState('2300');
  const [bulk,setBulk]=React.useState('');
  const [left,setLeft]=React.useState('');
  const [right,setRight]=React.useState('');
  const [note,setNote]=React.useState('Ручной аналог, применяемость не проверена');
  const [mergeConfirmed,setMergeConfirmed]=React.useState(false);
  const [file,setFile]=React.useState<File|null>(null);
  const [preview,setPreview]=React.useState<Preview|null>(null);
  const [exporting,setExporting]=React.useState(false);
  const seq=React.useRef(0);
  const context=React.useRef({org:'',loc:''});
  const scope={org:org.trim(),loc:loc.trim()};
  const scopeOk=uuid.test(scope.org)&&uuid.test(scope.loc);
  const root=()=>'/api/v1/dev/auto/organizations/'+scope.org+'/warehouse/catalog';

  React.useEffect(()=>{
    context.current={org:org.trim(),loc:loc.trim()};
    seq.current++;
    setRows([]);setCursor(null);setListStatus('');
    setReading(false); // The old async read will not clear itself after seq invalidation.
    setPreview(null);setMessage('');setMergeConfirmed(false);
    setPending(null);
  },[org,loc]);
  React.useEffect(()=>()=>{seq.current++;},[]);

  async function load(next?:string){
    if(!scopeOk||reading||pending)return;
    const current=++seq.current;
    const target={...scope};
    setReading(true);setListStatus('Загружаю...');
    try{
      const url=root()+'?locationId='+encodeURIComponent(target.loc)+
        '&limit=30'+(next?'&afterSkuId='+encodeURIComponent(next):'');
      const res=await fetch(url,{credentials:'include',cache:'no-store'});
      if(current!==seq.current||target.org!==context.current.org||
         target.loc!==context.current.loc)return;
      if(!res.ok){setRows([]);setCursor(null);setListStatus(reason(res.status));return;}
      const data:unknown=await res.json();
      if(!data||typeof data!=='object')throw Error('shape');
      const d=data as {syntheticOnly?:unknown;items?:unknown;nextSkuId?:unknown};
      if(d.syntheticOnly!==true||!Array.isArray(d.items)||d.items.length>30||
         !d.items.every(validItem)||!(d.nextSkuId===null||
          typeof d.nextSkuId==='string'&&uuid.test(d.nextSkuId)))throw Error('shape');
      setRows(next?old=>[...old,...d.items as Item[]]:d.items as Item[]);
      setCursor(d.nextSkuId);
      setListStatus('Данные синтетического склада. Цены скрываются по серверным правам.');
    }catch{
      if(current===seq.current){setRows([]);setCursor(null);
        setListStatus('Не удалось проверить список, старые данные скрыты.');}
    }finally{if(current===seq.current)setReading(false);}
  }

  async function doPost(kind:string,path:string,body:string|Uint8Array,
                        mime='application/json',retry?:Pending){
    if(busy||(!retry&&pending)||!scopeOk)return;
    const snap=retry??{kind,url:path,body,mime,key:crypto.randomUUID(),...scope};
    if(snap.org!==context.current.org||snap.loc!==context.current.loc)return;
    setBusy(true);setMessage('');
    try{
      const csrf=await fetch('/api/v1/auth/csrf',
        {method:'POST',credentials:'include',cache:'no-store'});
      if(!csrf.ok)throw Error('unknown');
      const token:unknown=await csrf.json();
      if(!token||typeof token!=='object'||!('csrfToken' in token)||
         typeof token.csrfToken!=='string')throw Error('unknown');
      const res=await fetch(snap.url,{method:'POST',credentials:'include',
        cache:'no-store',
        headers:{'Content-Type':snap.mime,'X-CSRF-Token':token.csrfToken,
          'Idempotency-Key':snap.key},
        body:typeof snap.body==='string'?snap.body:new Uint8Array(snap.body) as BodyInit});
      if(snap.org!==context.current.org||snap.loc!==context.current.loc)return;
      if(res.status>=500)throw Error('unknown');
      if(!res.ok){
        setPending(null);setMessage(reason(res.status));return;
      }
      const data:unknown=await res.json();
      if(!data||typeof data!=='object'||
         !('syntheticOnly' in data)||data.syntheticOnly!==true)
        throw Error('unknown');
      if(kind==='preview'){
        const d=data as Partial<Preview>;
        if(!d.previewId||!uuid.test(d.previewId)||!d.sha256||
          !/^[a-f0-9]{64}$/.test(d.sha256)||!Array.isArray(d.items)||
          !d.items.every(r=>typeof r.article==='string'&&
            (r.skuId===null||typeof r.skuId==='string'&&uuid.test(r.skuId))&&
            Number.isInteger(r.version)))throw Error('unknown');
        setPreview(d as Preview);
      }else{
        setPreview(null);
      }
      setPending(null);setMergeConfirmed(false);
      setMessage(kind==='preview'?'Предпросмотр подтверждён сервером. Проверьте строки и подтвердите импорт.':
        kind==='commit'?'Импорт применён атомарно. Прочитайте номенклатуру заново.':
        kind==='merge'?'Объединение зарегистрировано без изменения исторических FK и остатков.':
        'Операция записана в неизменяемый журнал. Перечитайте каталог.');
      seq.current++;setRows([]);setCursor(null);setListStatus('');
    }catch{
      if(snap.org===context.current.org&&snap.loc===context.current.loc){
        setPending(snap);setMessage('Результат POST неизвестен. Повторите дословно тот же запрос и UUID-ключ.');
      }
    }finally{setBusy(false);}
  }

  const normalized=article.trim();
  const createOk=scopeOk&&articlePattern.test(normalized)&&
    categoryPattern.test(category.trim())&&['PCS','L','KG'].includes(uom)&&
    (!barcode||/^\d{8,32}$/.test(barcode))&&brand.length<=80&&
    !/^[=+@-]/.test(brand)&&integer(purchase)&&integer(sale);
  let entries:Array<{skuId:string;expectedVersion:number;
      purchaseMinor:number;saleMinor:number}>=[];
  let bulkValid=false;
  try{
    const lines=bulk.trim().split(/\r?\n/).filter(Boolean);
    if(lines.length>=1&&lines.length<=100){
      entries=lines.map(line=>{
        const pieces=line.split(';');
        if(pieces.length!==4||!uuid.test(pieces[0])||
          !integer(pieces[1])||Number(pieces[1])<1||
          !integer(pieces[2])||!integer(pieces[3]))
          throw Error('line');
        return {skuId:pieces[0],expectedVersion:Number(pieces[1]),
          purchaseMinor:Number(pieces[2]),saleMinor:Number(pieces[3])};
      });
      bulkValid=new Set(entries.map(x=>x.skuId)).size===entries.length;
    }
  }catch{bulkValid=false;entries=[];}
  const itemOptions=rows.filter(x=>!x.mergedInto&&x.category&&x.uom);
  const uploadOk=scopeOk&&file!==null&&file.size>0&&file.size<=128000&&
    file.name.toLowerCase().endsWith('.xlsx');

  async function upload(){
    if(!uploadOk||!file)return;
    const bytes=new Uint8Array(await file.arrayBuffer());
    await doPost('preview',root()+'/imports/preview?locationId='+
      encodeURIComponent(scope.loc),bytes,
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  }
  async function exportFile(){
    if(!scopeOk||busy||exporting||pending)return;
    setExporting(true);
    const target={...scope};
    try{
      const res=await fetch(root()+'/export?locationId='+encodeURIComponent(scope.loc),
        {credentials:'include',cache:'no-store'});
      if(target.org!==context.current.org||target.loc!==context.current.loc)return;
      if(!res.ok){setMessage(reason(res.status));return;}
      const result=await res.blob();
      if(!result.size||result.size>4_000_000)throw Error('size');
      const url=URL.createObjectURL(result);
      const link=document.createElement('a');
      link.href=url;link.download='auto-catalog-dev.xlsx';
      document.body.appendChild(link);link.click();link.remove();
      URL.revokeObjectURL(url);
      setMessage('Экспорт XLSX создан из доступных вам действующих карточек с ценами.');
    }catch{setMessage('Экспорт не подтверждён, доступность или размер не прошли проверку.');}
    finally{setExporting(false);}
  }

  return <section className="ui-page">
    <h1>Номенклатура автозапчастей (dev)</h1>
    <p>Синтетический каталог: цены — тестовые копейки RUB, аналоги не подтверждают OEM-применяемость. Для изменений нужны права WAREHOUSE и MANAGER одновременно. Физический склад и исторические документы не переписываются.</p>
    <p><a href="/docs/#/guides/warehouse">Руководство кладовщика</a> ·
      <a href="/docs/#/auto/SKU_CATALOG">Контракт каталога и откат</a></p>
    <div className="auto-claim-forms">
      <label>UUID организации<input value={org} onChange={e=>setOrg(e.target.value)}/></label>
      <label>UUID филиала<input value={loc} onChange={e=>setLoc(e.target.value)}/></label>
    </div>
    {message&&<p role="status">{message}</p>}
    {pending&&<div role="alert">
      Неизвестный результат: {pending.kind}. Не изменяйте тело повторного запроса.
      <button disabled={busy||!scopeOk||pending.org!==scope.org||
        pending.loc!==scope.loc} onClick={()=>void doPost(
          pending.kind,pending.url,pending.body,pending.mime,pending)}>
        Повторить с прежним ключом
      </button>
    </div>}
    <section aria-label="Каталог филиала">
      <h2>1. Текущая номенклатура</h2>
      <button disabled={!scopeOk||busy||reading||!!pending} onClick={()=>void load()}>
        Загрузить номенклатуру
      </button>
      <button disabled={!scopeOk||busy||exporting||!!pending}
        onClick={()=>void exportFile()}>
        {exporting?'Готовлю XLSX…':'Экспортировать XLSX'}
      </button>
      {listStatus&&<p role="status">{listStatus}</p>}
      {rows.length>0&&<div style={{overflowX:'auto'}}>
        <table><thead><tr><th>Артикул / UUID</th><th>Категория / ед.</th>
          <th>Версия</th><th>Закупка, коп.</th><th>Продажа, коп.</th>
          <th>Состояние</th><th>Действие</th></tr></thead><tbody>
          {rows.map(item=><tr key={item.skuId}>
            <td><strong>{item.article}</strong><small style={{display:'block'}}>{item.skuId}</small></td>
            <td>{item.category||'Не оформлен'} / {item.uom||'—'}</td>
            <td>{item.version}</td>
            <td>{item.purchaseHidden?'скрыта':item.purchaseMinor??'—'}</td>
            <td>{item.saleHidden?'скрыта':item.saleMinor??'—'}</td>
            <td>{item.mergedInto?'Логически объединён с '+item.mergedInto:'Действует'}</td>
            <td><button disabled={!!item.mergedInto||item.version<1}
              onClick={()=>setBulk(item.skuId+';'+item.version+';'+
                String(item.purchaseMinor??0)+';'+String(item.saleMinor??0))}>
              В пакет цен</button></td>
          </tr>)}</tbody></table>
      </div>}
      {cursor&&<button disabled={busy||reading||!!pending}
        onClick={()=>void load(cursor)}>Следующая страница SKU</button>}
    </section>
    <section aria-label="Создание SKU">
      <h2>2. Карточка новой детали</h2>
      <div className="auto-claim-forms">
        <label>Артикул<input value={article} onChange={e=>setArticle(e.target.value)}/></label>
        <label>Категория<input value={category} onChange={e=>setCategory(e.target.value)}/></label>
        <label>Единица измерения<select value={uom} onChange={e=>setUom(e.target.value)}>
          <option value="PCS">PCS — штука</option><option value="L">L — литр</option>
          <option value="KG">KG — килограмм</option></select></label>
        <label>Штрихкод (необязательно)<input value={barcode}
          onChange={e=>setBarcode(e.target.value)}/></label>
        <label>Бренд (необязательно)<input value={brand}
          onChange={e=>setBrand(e.target.value)}/></label>
        <label>Тестовая закупочная цена, коп.<input type="number" min="0"
          value={purchase} onChange={e=>setPurchase(e.target.value)}/></label>
        <label>Тестовая продажная цена, коп.<input type="number" min="0"
          value={sale} onChange={e=>setSale(e.target.value)}/></label>
      </div>
      <button disabled={!createOk||busy||!!pending}
        onClick={()=>void doPost('create',root()+'/skus',
          JSON.stringify({locationId:scope.loc,article:normalized,
            category:category.trim(),uom,barcode:barcode||null,brand:brand||null,
            purchaseMinor:Number(purchase),saleMinor:Number(sale)}))}>
        Создать SKU и исходную версию цены
      </button>
    </section>
    <section aria-label="Пакетные цены">
      <h2>3. Пакетные версии цен</h2>
      <p>Одна строка: UUID SKU;текущая версия;закупка в копейках;продажа в копейках. До 100 строк; изменения атомарны. Не используйте скрытые цены как нулевые.</p>
      <label>Строки обновления цены
        <textarea rows={4} value={bulk} onChange={e=>setBulk(e.target.value)}
          placeholder="UUID;1;1250;2600"/>
      </label>
      <button disabled={!scopeOk||!bulkValid||busy||!!pending}
        onClick={()=>void doPost('prices',root()+'/prices',
          JSON.stringify({locationId:scope.loc,entries}))}>
        Сохранить пакет версий цен
      </button>
    </section>
    <section aria-label="Аналоги и объединение">
      <h2>4. Аналоги и безопасное объединение</h2>
      <p>Аналог — только вручную внесённая подсказка, не утверждение совместимости. Объединение допустимо лишь при совместимой карточке, отсутствии активных остатков и серийных позиций источника. История сохраняет прежние SKU UUID.</p>
      <div className="auto-claim-forms">
        <label>Исходный SKU<select value={left} onChange={e=>{setLeft(e.target.value);setMergeConfirmed(false);}}>
          <option value="">Выберите SKU</option>{itemOptions.map(x=>
            <option key={x.skuId} value={x.skuId}>{x.article}</option>)}
        </select></label>
        <label>Целевой SKU<select value={right} onChange={e=>{setRight(e.target.value);setMergeConfirmed(false);}}>
          <option value="">Выберите SKU</option>{itemOptions.map(x=>
            <option key={x.skuId} value={x.skuId}>{x.article}</option>)}
        </select></label>
        <label>Причина связи аналога<input value={note} onChange={e=>setNote(e.target.value)}/></label>
      </div>
      <button disabled={!scopeOk||!uuid.test(left)||!uuid.test(right)||
        left===right||note.trim().length<3||busy||!!pending}
        onClick={()=>void doPost('analog',root()+'/analogs',
          JSON.stringify({locationId:scope.loc,leftSkuId:left,
            rightSkuId:right,evidenceNote:note.trim()}))}>
        Сохранить аналог (не OEM-подтверждение)
      </button>
      <label style={{display:'block'}}><input type="checkbox" checked={mergeConfirmed}
        onChange={e=>setMergeConfirmed(e.target.checked)}/>
        Подтверждаю только логическое объединение, без переноса складских остатков
      </label>
      <button disabled={!scopeOk||!uuid.test(left)||!uuid.test(right)||
        left===right||!mergeConfirmed||busy||!!pending}
        onClick={()=>void doPost('merge',root()+'/merges',
          JSON.stringify({locationId:scope.loc,sourceSkuId:left,targetSkuId:right}))}>
        Объединить SKU с сохранением истории
      </button>
    </section>
    <section aria-label="XLSX импорт">
      <h2>5. XLSX: проверка → подтверждение</h2>
      <p>Один видимый лист, без формул, макросов, внешних ссылок. Заголовки: <code>article, category, uom, barcode, brand, purchaseMinor, saleMinor, analogArticle</code>. Максимум 100 строк, 128 КБ, только синтетические данные.</p>
      <label>Файл XLSX<input type="file" accept=".xlsx" onChange={e=>{
        setFile(e.target.files?.[0]||null);setPreview(null);}}/></label>
      <button disabled={!uploadOk||busy||!!pending}
        onClick={()=>void upload()}>Проверить XLSX без записи в каталог</button>
      {preview&&<div>
        <h3>Предпросмотр: {preview.items.length} строк</h3>
        <p>SHA-256: <code>{preview.sha256}</code></p>
        <div style={{overflowX:'auto'}}><table><thead><tr><th>Артикул</th>
          <th>Действие</th><th>Текущая версия</th></tr></thead><tbody>
          {preview.items.map(r=><tr key={r.article}><td>{r.article}</td>
            <td>{r.skuId?'Новая версия существующего SKU':'Создание SKU'}</td>
            <td>{r.version}</td></tr>)}
        </tbody></table></div>
        <button disabled={!scopeOk||busy||!!pending}
          onClick={()=>void doPost('commit',root()+'/imports/commit',
            JSON.stringify({locationId:scope.loc,previewId:preview.previewId,
              sha256:preview.sha256}))}>
          Подтвердить весь XLSX атомарно
        </button>
      </div>}
    </section>
  </section>;
}
