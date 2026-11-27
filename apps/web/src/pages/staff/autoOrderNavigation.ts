// Навигационные параметры — только локаторы synthetic dev, не полномочия.
export const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const stages=['ALL','DRAFT','IN_WORK','READY','POSTED',
  'CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL','CANCELED'] as const;
export const repairStages=['ALL','DRAFT','IN_WORK','READY'] as const;
export const documentStages=['ALL','RESTRICTED','DRAFT','PREVIEW_SEALED','POSTED',
  'CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL'] as const;
export const bookingStates=['ALL','SCHEDULED','CANCELED'] as const;
export type Stage=typeof stages[number];
export type RepairStage=typeof repairStages[number];
export type DocumentStage=typeof documentStages[number];
export type BookingState=typeof bookingStates[number];
export type RegisterFilters={stage:Stage;repairStage:RepairStage;
  documentStage:DocumentStage;bookingStatus:BookingState;
  fromDay:string;toDay:string;orderId:string};
export const defaultFilters:RegisterFilters={stage:'ALL',repairStage:'ALL',
  documentStage:'ALL',bookingStatus:'ALL',fromDay:'',toDay:'',orderId:''};
export const validDay=(s:string)=>!s||(/^\d{4}-\d{2}-\d{2}$/.test(s)&&
  Number.isFinite(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s&&s.slice(0,4)!=='0000');

export function scopeValue(query:URLSearchParams,key:string):string{
  const values=query.getAll(key);
  return values.length===1&&uuid.test(values[0])?values[0].toLowerCase():'';
}
function member<T extends string>(query:URLSearchParams,key:string,values:readonly T[]):T|null{
  const entries=query.getAll(key);
  if(!entries.length)return values[0];
  return entries.length===1&&values.includes(entries[0] as T)?entries[0] as T:null;
}
function optional(query:URLSearchParams,key:string,check:(value:string)=>boolean):string|null{
  const entries=query.getAll(key);
  if(!entries.length)return '';
  return entries.length===1&&check(entries[0])?entries[0]:null;
}
export function readRegisterFilters(query:URLSearchParams):RegisterFilters|null{
  const stage=member(query,'stage',stages),repairStage=member(query,'repairStage',repairStages);
  const documentStage=member(query,'documentStage',documentStages);
  const bookingStatus=member(query,'bookingStatus',bookingStates);
  const fromDay=optional(query,'fromDay',validDay),toDay=optional(query,'toDay',validDay);
  const orderId=optional(query,'orderId',value=>value===''||uuid.test(value));
  if(stage===null||repairStage===null||documentStage===null||bookingStatus===null||
    fromDay===null||toDay===null||orderId===null)return null;
  return {stage,repairStage,documentStage,bookingStatus,fromDay,toDay,orderId:orderId.toLowerCase()};
}
export function registerParams(org:string,loc:string,filters:RegisterFilters):URLSearchParams{
  const result=new URLSearchParams();
  if(uuid.test(org))result.set('org',org.toLowerCase());
  if(uuid.test(loc))result.set('loc',loc.toLowerCase());
  for(const key of Object.keys(defaultFilters) as (keyof RegisterFilters)[]){
    if(filters[key]!==defaultFilters[key])result.set(key,filters[key]);
  }
  return result;
}
// В рабочем месте переносим только проверенный набор фильтров этого филиала.
// Не используем произвольный return URL, курсор, session token или данные ответа.
export function returnRegisterParams(query:URLSearchParams,org:string,loc:string):URLSearchParams{
  const fallback=registerParams(org,loc,defaultFilters);
  const entries=query.getAll('register');
  if(entries.length!==1||entries[0].length>1024||!uuid.test(org)||!uuid.test(loc))return fallback;
  const candidate=new URLSearchParams(entries[0]);
  if(scopeValue(candidate,'org')!==org.toLowerCase()||
    scopeValue(candidate,'loc')!==loc.toLowerCase())return fallback;
  const filters=readRegisterFilters(candidate);
  if(!filters||(filters.fromDay&&filters.toDay&&filters.fromDay>filters.toDay))return fallback;
  return registerParams(org,loc,filters);
}
export function workspaceParams(org:string,loc:string,order:string,tab:string,
                                register:URLSearchParams):URLSearchParams{
  return new URLSearchParams({org,loc,order,tab,register:register.toString()});
}
