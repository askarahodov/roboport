/** Закрытый synthetic contract; нет production/mobile bearer auth. */
import type {components} from './autoCustomerOrdersDevSchema';

export type Card=components['schemas']['Card'];
export type Page=components['schemas']['Page'];
export type Detail=components['schemas']['Detail'];
export type Quote=components['schemas']['Quote'];
export type Reason=components['schemas']['Clarification']['reasonCode'];
export type Receipt=components['schemas']['MutationReceipt'];

type R=Record<string,unknown>;
const object=(x:unknown):x is R=>!!x&&typeof x==='object'&&!Array.isArray(x);
const keys=(x:R,expected:string[])=>Object.keys(x).length===expected.length&&expected.every(k=>Object.hasOwn(x,k));
const uid=(x:unknown):x is string=>typeof x==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
const text=(x:unknown)=>typeof x==='string'&&x.trim().length>0;
const integer=(x:unknown,min:number,max=Number.MAX_SAFE_INTEGER):x is number=>
  typeof x==='number'&&Number.isSafeInteger(x)&&x>=min&&x<=max;
const money=(x:unknown):x is number=>integer(x,0,1e12);
const hash=(x:unknown)=>typeof x==='string'&&/^[0-9a-f]{64}$/.test(x);
const date=(x:unknown):x is string=>typeof x==='string'&&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(x)&&Number.isFinite(Date.parse(x));
const one=(x:unknown,values:string[])=>typeof x==='string'&&values.includes(x);
const optional=(x:unknown,check:(v:unknown)=>boolean)=>x===null||check(x);
const array=(x:unknown,max:number,check:(v:unknown)=>boolean,min=0):x is R[]=>
  Array.isArray(x)&&x.length>=min&&x.length<=max&&x.every(check);
const record=(x:unknown,fields:string[],check:(v:R)=>boolean)=>object(x)&&keys(x,fields)&&check(x);
const category=(x:unknown)=>one(x,['GENERAL','DIAGNOSTIC','MASTER']);
const reason=(x:unknown)=>one(x,['PRICE','WORK_SCOPE','TIMING','OTHER']);
const resolution=(x:unknown)=>one(x,['EXPLAINED','NEW_VERSION_REQUIRED']);
const decision=(x:unknown)=>one(x,['APPROVED','DECLINED']);
const cardKeys=['id','organizationId','locationId','createdAt','vehicle','appointment'];
function card(c:R){
  return [c.id,c.organizationId,c.locationId].every(uid)&&date(c.createdAt)&&
    record(c.vehicle,['make','model','modelYear','vinLast4'],v=>
      text(v.make)&&text(v.model)&&optional(v.modelYear,n=>integer(n,1886,2100))&&
      optional(v.vinLast4,x=>typeof x==='string'&&/^[A-HJ-NPR-Z0-9]{4}$/.test(x)))&&
    record(c.appointment,['startsAt','endsAt','status'],a=>
      date(a.startsAt)&&date(a.endsAt)&&Date.parse(a.startsAt)<Date.parse(a.endsAt)&&one(a.status,['SCHEDULED','CANCELED']));
}
const cardOnly=(x:unknown)=>record(x,cardKeys,card);
function line(x:unknown){
  if(!object(x)||!uid(x.serviceId)||!text(x.code)||!text(x.title)||!integer(x.durationMinutes,1)||!money(x.amountMinor))return false;
  const base=['serviceId','code','title','durationMinutes','amountMinor'];
  if(keys(x,base))return true;
  if(!category(x.qualificationCode)||!integer(x.rateMinorPerHour,0,1e10))return false;
  const rated=[...base,'pricingMethod','qualificationCode','rateMinorPerHour'];
  if(x.pricingMethod==='NORM_HOUR')return keys(x,rated);
  return x.pricingMethod==='NORM_HOUR_FACTORED'&&keys(x,[...rated,'quantity','coefficientBps','discountBps'])&&
    integer(x.quantity,1,20)&&integer(x.coefficientBps,10000,30000)&&integer(x.discountBps,0,2500);
}
const material=(x:unknown)=>record(x,['selectionId','skuId','article','itemKind','quantity','unitPriceMinor','amountMinor'],m=>
  uid(m.selectionId)&&uid(m.skuId)&&text(m.article)&&one(m.itemKind,['PART','CONSUMABLE'])&&
  integer(m.quantity,1)&&integer(m.unitPriceMinor,0,1e12)&&money(m.amountMinor)&&
  BigInt(m.quantity)*BigInt(m.unitPriceMinor)===BigInt(m.amountMinor as number)&&
  (m.itemKind!=='CONSUMABLE'||m.unitPriceMinor===0));
const diagnostic=(x:unknown)=>record(x,['serviceId','findingCode','severityCode'],d=>uid(d.serviceId)&&
  one(d.findingCode,['NO_FAULT','MAINTENANCE_DUE','FAULT_CONFIRMED','FURTHER_CHECK'])&&one(d.severityCode,['ROUTINE','SOON','URGENT']));
const tariff=(x:unknown)=>record(x,['serviceId','policyId','version','qualificationCode','rateMinorPerHour','maxDiscountBps'],
  t=>uid(t.serviceId)&&uid(t.policyId)&&integer(t.version,1)&&category(t.qualificationCode)&&
    integer(t.rateMinorPerHour,0,1e10)&&integer(t.maxDiscountBps,0,2500));
function quote(x:unknown){
  return record(x,['id','version','totalMinor','currency','digest','lines','diagnostics','tariffs','materialLines',
    'decision','createdAt','decidedAt','clarificationId','clarificationReason','clarificationResolution'],q=>{
    if(!uid(q.id)||!integer(q.version,1,30)||!money(q.totalMinor)||q.currency!=='RUB'||!hash(q.digest)||
      !array(q.lines,50,line,1)||!array(q.materialLines,100,material)||!array(q.diagnostics,50,diagnostic)||
      !array(q.tariffs,50,tariff)||!optional(q.decision,decision)||!date(q.createdAt)||!optional(q.decidedAt,date)||
      !optional(q.clarificationId,uid)||!optional(q.clarificationReason,reason)||!optional(q.clarificationResolution,resolution))return false;
    if((q.decision===null)!==(q.decidedAt===null)||(q.clarificationId===null)!==(q.clarificationReason===null)||
       (q.clarificationId===null&&q.clarificationResolution!==null))return false;
    const serviceIds=new Set(q.lines.map(l=>l.serviceId));
    if(serviceIds.size!==q.lines.length||q.diagnostics.some(d=>!serviceIds.has(d.serviceId))||
       q.tariffs.some(t=>!serviceIds.has(t.serviceId)))return false;
    const total=[...q.lines,...q.materialLines].reduce((sum,l)=>sum+BigInt(l.amountMinor as number),0n);
    return total===BigInt(q.totalMinor);
  });
}
function quotes(items:unknown,version:unknown){
  return array(items,30,quote)&&integer(version,0,30)&&items.length===version&&
    items.every((q,i)=>q.version===i+1)&&new Set(items.map(q=>q.id)).size===items.length;
}
const inspection=(x:unknown)=>record(x,['id','odometerKm','fuelPercent','tyresOk','lightsOk','fluidLeak','exteriorCode','createdAt'],
  i=>uid(i.id)&&integer(i.odometerKm,0,2000000)&&integer(i.fuelPercent,0,100)&&
    [i.tyresOk,i.lightsOk,i.fluidLeak].every(v=>typeof v==='boolean')&&one(i.exteriorCode,['CLEAR','DAMAGE','UNCHECKED'])&&date(i.createdAt));
const labor=(x:unknown)=>record(x,['serviceId','title','actualMinutes'],l=>uid(l.serviceId)&&text(l.title)&&integer(l.actualMinutes,1,1440));
const document=(x:unknown)=>record(x,['id','sha256','createdAt','type'],d=>uid(d.id)&&hash(d.sha256)&&date(d.createdAt)&&d.type==='SYNTHETIC_PRE_HANDOVER_PREVIEW');

export function parseCustomerOrderPage(raw:unknown,limit:number):Page{
  if(!record(raw,['items','nextCursor','syntheticOnly','releaseAllowed'],v=>v.syntheticOnly===true&&v.releaseAllowed===false&&
    array(v.items,limit,cardOnly)&&new Set(v.items.map(x=>x.id)).size===v.items.length&&
    optional(v.nextCursor,x=>typeof x==='string'&&/^[A-Za-z0-9_-]{1,256}$/.test(x))))throw Error('invalid customer order page');
  return raw as Page;
}
export function parseCustomerOrderDetail(raw:unknown,expected:Card):Detail{
  if(!record(raw,[...cardKeys,'estimates','latestVersion','inspection','qcStatus','workStatus','labor','document','canDecide',
     'syntheticOnly','legalGatePassed','releaseAllowed'],d=>card(d)&&d.id===expected.id&&
     d.organizationId===expected.organizationId&&d.locationId===expected.locationId&&
     d.syntheticOnly===true&&d.legalGatePassed===false&&d.releaseAllowed===false&&quotes(d.estimates,d.latestVersion)&&
     optional(d.inspection,inspection)&&optional(d.document,document)&&array(d.labor,70,labor)&&
     one(d.qcStatus,['NOT_INSPECTED','PENDING_QC','QC_PASSED','QC_FAILED'])&&
     one(d.workStatus,['NOT_APPROVED','APPROVED','IN_PROGRESS','READY_FOR_QC','QC_ACCEPTED'])&&typeof d.canDecide==='boolean'))
    throw Error('invalid customer order detail');
  const d=raw as Detail,last=d.estimates.at(-1);
  const eligible=!!last&&last.decision===null&&(last.clarificationId===null||last.clarificationResolution==='EXPLAINED')&&d.appointment.status==='SCHEDULED';
  if(d.canDecide&&!eligible)throw Error('invalid quote eligibility');
  return d;
}
export function parseCustomerQuoteReceipt(raw:unknown,body:string):Receipt{
  const request=JSON.parse(body) as R;
  if(!record(raw,['ok','replayed','items','latestVersion','syntheticOnly','releaseAllowed'],r=>
      r.ok===true&&typeof r.replayed==='boolean'&&r.syntheticOnly===true&&r.releaseAllowed===false&&quotes(r.items,r.latestVersion)))
    throw Error('invalid quote receipt');
  const r=raw as Receipt,last=r.items.at(-1);
  if(!last||last.id!==request.revisionId||
     ('decision' in request?last.decision!==request.decision:
       last.clarificationId===null||last.clarificationReason!==request.reasonCode))throw Error('mismatched quote receipt');
  return r;
}


export type OrderFilters={search:string;status:'ALL'|'SCHEDULED'|'CANCELED';fromDay:string;toDay:string};
export const emptyOrderFilters:OrderFilters={search:'',status:'ALL',fromDay:'',toDay:''};
export function orderFilterParams(filters:OrderFilters):URLSearchParams{
  const params=new URLSearchParams();
  if(filters.search.trim())params.set('search',filters.search.trim());
  if(filters.status!=='ALL')params.set('status',filters.status);
  if(filters.fromDay)params.set('fromDay',filters.fromDay);
  if(filters.toDay)params.set('toDay',filters.toDay);
  return params;
}
export function validOrderFilters(filters:OrderFilters):boolean{
  const day=(s:string)=>!s||(/^\d{4}-\d{2}-\d{2}$/.test(s)&&
    Number.isFinite(Date.parse(s+'T00:00:00Z'))&&
    new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s&&s.slice(0,4)!=='0000');
  return filters.search.length<=64&&!/[\x00-\x1f\x7f]/.test(filters.search)&&
    ['ALL','SCHEDULED','CANCELED'].includes(filters.status)&&day(filters.fromDay)&&day(filters.toDay)&&
    (!filters.fromDay||!filters.toDay||filters.fromDay<=filters.toDay);
}
