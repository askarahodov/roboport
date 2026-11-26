// UUID-серий из существующего процесса регистрации/подтверждения происхождения.
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function validSerialIds(value:unknown,quantity:number):value is string[]{
  return Array.isArray(value)&&value.length<=50&&(value.length===0||value.length===quantity)&&
    value.every(x=>typeof x==='string'&&uuid.test(x))&&
    new Set(value.map(x=>x.toLowerCase())).size===value.length;
}
export function parseSerialIds(text:string,quantity:number):string[]|null{
  const values=text.trim()?text.trim().split(/[\s,;]+/).map(x=>x.toLowerCase()):[];
  return validSerialIds(values,quantity)?values.sort():null;
}
