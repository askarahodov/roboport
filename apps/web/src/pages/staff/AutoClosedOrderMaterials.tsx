import React from 'react';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const statuses=['TRACED','FIFO_TRACED','SERIAL_TRACED','UNPROVEN','UNVALUED','LATE_VALUATION','OTHER_CURRENCY','UNPAIRED'] as const;
type Status=typeof statuses[number];
type Movement={stockEventId:string;action:'ISSUE'|'RETURN';quantity:number;lotMovementId:string|null};
type Source={receiptId:string;acceptanceId:string;valuationId:string;unitNetMinor:number};
type FifoSlice=Source&{stockEventId:string;lotMovementId:string;originalIssueId:string;
  action:'ISSUE'|'RETURN';quantity:number};
type SerialSlice=FifoSlice&{serialId:string;serialMovementId:string;sourceLocationId:string};
type Group={skuId:string;lotId:string|null;issuedQuantity:number;returnedQuantity:number;
  netQuantity:number;status:Status;source:Source|null;materialNetMinor:number|null;fifoSlices:FifoSlice[];serialSlices:SerialSlice[];movements:Movement[]};
type Item={workOrderId:string;closedDay:string;closedEventId:string;
  status:'COMPLETE'|'INCOMPLETE';materialNetMinor:number|null;groups:Group[]};
type Report={syntheticOnly:true;realMoney:false;releaseAllowed:false;marginAvailable:false;
  payrollAvailable:false;currency:'RUB';basis:string;timeZone:string;fromDay:string;toDay:string;
  summary:{orderCount:number;completeOrderCount:number;movementCount:number;materialNetMinor:number|null};
  items:Item[]};
const labels:Record<Status,string>={
  TRACED:'Подтверждён единственный приход',FIFO_TRACED:'Подтверждён расход FIFO по приходам',SERIAL_TRACED:'Серийный расход с независимым подтверждением прихода',UNPROVEN:'Происхождение не подтверждено',
  UNVALUED:'Нет оценки прихода',LATE_VALUATION:'Оценка после первой выдачи',
  OTHER_CURRENCY:'Другая валюта',UNPAIRED:'Нет связанной партии',
};
const validId=(x:unknown):x is string=>typeof x==='string'&&uuid.test(x);
const int=(x:unknown):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0;
const money=(n:number|null)=>n===null?'Не определена':(n/100).toLocaleString('ru-RU',{
  minimumFractionDigits:2,maximumFractionDigits:2,
})+' RUB';
export function parseMaterialReport(raw:unknown,from:string,to:string):Report{
  const r=raw as Report;
  if(!r||r.syntheticOnly!==true||r.realMoney!==false||r.releaseAllowed!==false||
    r.marginAvailable!==false||r.payrollAvailable!==false||r.currency!=='RUB'||
    r.basis!=='CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE'||
    r.fromDay!==from||r.toDay!==to||typeof r.timeZone!=='string'||!r.timeZone||
    !r.summary||!Array.isArray(r.items)||r.items.length>200||
    r.summary.orderCount!==r.items.length)throw Error('contract');
  const orders=new Set<string>(),events=new Set<string>();
  let count=0,complete=0,total=0,sliceCount=0,serialCount=0;
  for(const item of r.items){
    if(!item||!validId(item.workOrderId)||!validId(item.closedEventId)||
      orders.has(item.workOrderId)||!/^\d{4}-\d{2}-\d{2}$/.test(item.closedDay)||
      item.closedDay<from||item.closedDay>to||!Array.isArray(item.groups))throw Error('order');
    orders.add(item.workOrderId);
    let subtotal=0,all=true;
    const groups=new Set<string>();
    for(const g of item.groups){
      if(!g||!validId(g.skuId)||(g.lotId!==null&&!validId(g.lotId))||
        !statuses.includes(g.status)||!int(g.issuedQuantity)||!int(g.returnedQuantity)||
        !Number.isSafeInteger(g.netQuantity)||g.netQuantity!==g.issuedQuantity-g.returnedQuantity||
        !Array.isArray(g.movements)||!g.movements.length||!Array.isArray(g.fifoSlices)||!Array.isArray(g.serialSlices)||
        groups.has(g.skuId+':'+g.lotId))throw Error('group');
      groups.add(g.skuId+':'+g.lotId);
      let issued=0,returned=0;
      for(const m of g.movements){
        if(!m||!validId(m.stockEventId)||events.has(m.stockEventId)||
          !int(m.quantity)||m.quantity===0||!['ISSUE','RETURN'].includes(m.action)||
          (g.lotId===null?m.lotMovementId!==null:!validId(m.lotMovementId))||++count>500)
          throw Error('event');
        events.add(m.stockEventId);
        if(m.action==='ISSUE')issued+=m.quantity;else returned+=m.quantity;
      }
      if(issued!==g.issuedQuantity||returned!==g.returnedQuantity)throw Error('quantities');
      if(g.status==='TRACED'){
        const s=g.source;
        if(!g.lotId||!s||g.fifoSlices.length!==0||g.serialSlices.length!==0||![s.receiptId,s.acceptanceId,s.valuationId].every(validId)||
          !int(s.unitNetMinor)||!int(g.materialNetMinor)||g.netQuantity<0||
          g.netQuantity*s.unitNetMinor!==g.materialNetMinor)throw Error('cost');
        subtotal+=g.materialNetMinor;
      }else if(g.status==='FIFO_TRACED'){
        if(!g.lotId||g.source!==null||!int(g.materialNetMinor)||g.netQuantity<0||g.serialSlices.length!==0||!g.fifoSlices.length)
          throw Error('fifo');
        const quantities=new Map<string,number>(),open=new Map<string,number>(),sources=new Map<string,string>();
        const moves=new Map(g.movements.map(m=>[m.stockEventId,m]));
        let cost=0;
        for(const s of g.fifoSlices){
          const m=moves.get(s.stockEventId);
          if(!m||s.lotMovementId!==m.lotMovementId||s.action!==m.action||
            ![s.originalIssueId,s.receiptId,s.acceptanceId,s.valuationId].every(validId)||
            !int(s.quantity)||s.quantity===0||!int(s.unitNetMinor)||s.unitNetMinor>1e9||
            ++sliceCount>5000)throw Error('fifo slice');
          const source=s.acceptanceId+':'+s.valuationId+':'+s.unitNetMinor;
          if(sources.has(s.receiptId)&&sources.get(s.receiptId)!==source)throw Error('source');
          sources.set(s.receiptId,source);
          const sign=s.action==='ISSUE'?1:-1;
          if(sign===1&&s.originalIssueId!==s.lotMovementId)throw Error('origin');
          const key=s.originalIssueId+':'+s.receiptId;
          open.set(key,(open.get(key)||0)+sign*s.quantity);
          quantities.set(s.stockEventId,(quantities.get(s.stockEventId)||0)+s.quantity);
          const amount=s.quantity*s.unitNetMinor;
          if(!int(amount))throw Error('overflow');
          cost+=sign*amount;
          if(!Number.isSafeInteger(cost))throw Error('overflow');
        }
        if(g.movements.some(m=>quantities.get(m.stockEventId)!==m.quantity)||
          [...open.values()].some(q=>!int(q))||cost!==g.materialNetMinor)throw Error('fifo totals');
        subtotal+=cost;
      }else if(g.status==='SERIAL_TRACED'){
        if(!g.lotId||g.source!==null||g.fifoSlices.length!==0||!int(g.materialNetMinor)||
          g.netQuantity<0||!g.serialSlices.length)throw Error('serial');
        const movementMap=new Map(g.movements.map(m=>[m.stockEventId,m]));
        const consumed=new Set<string>(),issued=new Map<string,SerialSlice>(),returned=new Set<string>();
        const units=new Map<string,number>(),receipts=new Map<string,string>();
        let cost=0;
        for(const s of g.serialSlices){
          const m=movementMap.get(s.stockEventId);
          if(!m||!['ISSUE','RETURN'].includes(s.action)||m.action!==s.action||
            m.lotMovementId!==s.lotMovementId||s.quantity!==1||
            ![s.serialId,s.serialMovementId,s.sourceLocationId,s.receiptId,
              s.acceptanceId,s.valuationId,s.originalIssueId].every(validId)||
            !int(s.unitNetMinor)||s.unitNetMinor>1e9||
            consumed.has(s.serialMovementId)||++serialCount>5000)throw Error('serial event');
          consumed.add(s.serialMovementId);
          const source=s.receiptId+':'+s.acceptanceId+':'+s.valuationId+':'+s.unitNetMinor+':'+s.sourceLocationId;
          if(receipts.has(s.serialId)&&receipts.get(s.serialId)!==source)throw Error('serial receipt');
          receipts.set(s.serialId,source);
          if(s.action==='ISSUE'){
            if(s.lotMovementId!==s.originalIssueId||issued.has(s.originalIssueId))
              throw Error('serial origin');
            issued.set(s.originalIssueId,s);
          }
          units.set(s.stockEventId,(units.get(s.stockEventId)||0)+1);
          cost+=s.unitNetMinor*(s.action==='ISSUE'?1:-1);
          if(!Number.isSafeInteger(cost))throw Error('serial overflow');
        }
        for(const s of g.serialSlices){
          if(s.action==='RETURN'){
            const origin=issued.get(s.originalIssueId);
            if(!origin||origin.serialId!==s.serialId||origin.receiptId!==s.receiptId||
              origin.sourceLocationId!==s.sourceLocationId||origin.unitNetMinor!==s.unitNetMinor||
              returned.has(s.originalIssueId))throw Error('serial return');
            returned.add(s.originalIssueId);
          }
        }
        if(g.movements.some(m=>units.get(m.stockEventId)!==m.quantity)||
          cost!==g.materialNetMinor)throw Error('serial totals');
        subtotal+=cost;
      }else{
        if(g.source!==null||g.materialNetMinor!==null||g.fifoSlices.length!==0||
          g.serialSlices.length!==0)throw Error('unknown');
        all=false;
      }
    }
    if(item.status!==(all?'COMPLETE':'INCOMPLETE')||
      item.materialNetMinor!==(all?subtotal:null)||!int(subtotal))throw Error('order total');
    if(all){complete++;total+=subtotal;}
  }
  if(r.summary.completeOrderCount!==complete||r.summary.movementCount!==count||
    r.summary.materialNetMinor!==(complete===r.items.length?total:null)||!int(total))throw Error('summary');
  return r;
}

export function AutoClosedOrderMaterials({org,loc,from,to,allowed}:{
  org:string;loc:string;from:string;to:string;allowed:boolean
}){
  const [report,setReport]=React.useState<Report|null>(null);
  const [status,setStatus]=React.useState('idle');
  const [exporting,setExporting]=React.useState(false);
  const active=React.useRef<AbortController|null>(null);
  React.useEffect(()=>()=>active.current?.abort(),[]);
  const url=(download=false)=>'/api/v1/dev/auto/organizations/'+encodeURIComponent(org)+
    '/locations/'+encodeURIComponent(loc)+'/reports/closed-order-materials'+(download?'/export':'')+
    '?'+new URLSearchParams({fromDay:from,toDay:to});
  async function load(download=false){
    if(!allowed)return;
    active.current?.abort();
    const pending=new AbortController();active.current=pending;
    if(download)setExporting(true);else{setReport(null);setStatus('loading');}
    try{
      const res=await fetch(url(download),{credentials:'include',cache:'no-store',signal:pending.signal});
      if(pending.signal.aborted)return;
      if([401,403,404].includes(res.status)){setReport(null);setStatus('denied');return;}
      if(res.status===409){setReport(null);setStatus('conflict');return;}
      if(res.status===422){setReport(null);setStatus('invalid');return;}
      if(!res.ok)throw Error('http');
      if(download){
        if(!res.headers.get('content-type')?.startsWith('text/csv'))throw Error('csv');
        const blob=await res.blob();if(pending.signal.aborted)return;
        const href=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=href;a.download='auto-closed-order-materials.csv';
        document.body.appendChild(a);a.click();a.remove();
        window.setTimeout(()=>URL.revokeObjectURL(href),1000);
      }else{
        const data=parseMaterialReport(await res.json(),from,to);
        if(!pending.signal.aborted){setReport(data);setStatus('ready');}
      }
    }catch{if(!pending.signal.aborted){setReport(null);setStatus('error');}}
    finally{if(!pending.signal.aborted)setExporting(false);}
  }
  return <section aria-label="Оценка расхода материалов закрытых заказов">
    <h2>Оценка расхода материалов закрытых заказов</h2>
    <p>Только synthetic-dev, без НДС. Оценка по источнику единственной приёмки
      или FIFO по нескольким принятым приходам, либо по отдельным серийным деталям с независимым подтверждением прихода. Все выдачи и возвраты связаны с первичкой. Не бухгалтерская себестоимость,
      прибыль или зарплата. Неизвестная стоимость не заменяется нулём.</p>
    <button type="button" disabled={!allowed||status==='loading'||exporting}
      onClick={()=>void load()}>Проверить расход материалов</button>
    {status==='loading'&&<p role="status">Проверка движения партий…</p>}
    {status==='denied'&&<p role="alert">Доступ к закупочным сведениям запрещён.</p>}
    {status==='conflict'&&<p role="alert">Данные изменились или превышен предел: сократите период и повторите проверку.</p>}
    {status==='invalid'&&<p role="alert">Неверный период оценки материалов.</p>}
    {status==='error'&&<p role="alert">Не удалось проверить расход материалов.</p>}
    {status==='ready'&&report&&<div>
      <p role="status">Заказов с полной оценкой: {report.summary.completeOrderCount} из {report.summary.orderCount}.
        Движений: {report.summary.movementCount}. Итоговая оценка: {money(report.summary.materialNetMinor)}.
        Часовой пояс: {report.timeZone}.</p>
      <button type="button" disabled={exporting} onClick={()=>void load(true)}>Скачать оценку материалов CSV</button>
      {!report.items.length&&<p>Нет закрытых заказов за период.</p>}
      <div style={{overflowX:'auto'}}><table>
        <caption>Оценка расхода по заказам и партиям</caption>
        <thead><tr><th scope="col">Заказ-наряд</th><th scope="col">День закрытия</th>
          <th scope="col">Оценка без НДС</th><th scope="col">Первичные данные</th></tr></thead>
        <tbody>{report.items.map(item=><tr key={item.workOrderId}>
          <th scope="row">{item.workOrderId}</th><td>{item.closedDay}</td>
          <td>{money(item.materialNetMinor)} · {item.status==='COMPLETE'?'Полная':'Неполная'}</td>
          <td><details><summary>Партии и движения</summary>
            <p>Закрытие: {item.closedEventId}</p>
            {!item.groups.length&&<p>Нет зарегистрированного расхода.</p>}
            {item.groups.map(g=><div key={g.skuId+':'+g.lotId}>
              <p>SKU: {g.skuId} · Партия: {g.lotId||'Не установлена'}</p>
              <p>{labels[g.status]} · Выдано: {g.issuedQuantity} · Возвращено: {g.returnedQuantity}
                {' '}· Расход: {g.netQuantity} · Оценка: {money(g.materialNetMinor)}</p>
              {g.source&&<p>Приход: {g.source.receiptId} · Приёмка: {g.source.acceptanceId}
                {' '}· Оценка: {g.source.valuationId} · Цена единицы: {money(g.source.unitNetMinor)}</p>}
              {g.fifoSlices.map((s,i)=><p key={s.stockEventId+':'+i}>FIFO: {s.action==='ISSUE'?'Выдача':'Возврат'} ·
                {' '}{s.quantity} × {money(s.unitNetMinor)} · Приход: {s.receiptId} · Приёмка: {s.acceptanceId}
                {' '}· Оценка: {s.valuationId} · Событие: {s.stockEventId} · Исходная выдача: {s.originalIssueId}</p>)}
              {g.serialSlices.map(s=><p key={s.serialMovementId}>Серия: {s.serialId} ·
                {s.action==='ISSUE'?'Выдача':'Возврат'} · {money(s.unitNetMinor)} ·
                Приход: {s.receiptId} · Приёмка: {s.acceptanceId} · Оценка: {s.valuationId} ·
                Исходная выдача: {s.originalIssueId} · Филиал прихода: {s.sourceLocationId}</p>)}
              {g.movements.map(m=><p key={m.stockEventId}>{m.action==='ISSUE'?'Выдача':'Возврат'}:
                {' '}{m.quantity} · {m.stockEventId} · {m.lotMovementId||'Без партии'}</p>)}
            </div>)}
          </details></td>
        </tr>)}</tbody>
      </table></div>
    </div>}
  </section>;
}
