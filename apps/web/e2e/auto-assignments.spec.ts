import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const mechanic='44444444-4444-4444-8444-444444444444';
const assignment='55555555-5555-4555-8555-555555555555';
const second='66666666-6666-4666-8666-666666666666';
const base='**/api/v1/dev/auto/organizations/**/assignments**';

const start=new Date(Date.now()+3*3600000).toISOString();
const end=new Date(Date.now()+4*3600000).toISOString();
type Row={assignmentId:string;workOrderId:string;mechanicId:string;
  startsAt:string;endsAt:string;status:'OFFERED'|'ACCEPTED';
  acceptedAt:string|null;canRespond:boolean};
const row=(id:string,canRespond:boolean):Row=>({
  assignmentId:id,workOrderId:order,mechanicId:mechanic,startsAt:start,
  endsAt:end,status:'OFFERED',acceptedAt:null,canRespond,
});
function result(rows:Row[],canOffer:boolean){
  return {syntheticOnly:true,releaseAllowed:false,canOffer,items:rows};
}
async function visit(page:Page){
  await page.goto('/staff/auto-assignments');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}
const fulfill=(route:import('@playwright/test').Route,data:unknown,status=200)=>
  route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});

test('руководитель предлагает сменное назначение, список, 390/1280 и тема',async({page})=>{
  const items:Row[]=[];
  const keys:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>
    fulfill(route,{csrfToken:'synthetic-csrf'}));
  await page.route(base,route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;
    if(req.method()==='GET')return fulfill(route,result(items,true));
    expect(pathname).toContain('/orders/'+order+'/assignments');
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    const body=req.postDataJSON();
    expect(body).toMatchObject({mechanicId:mechanic});
    expect(new Date(body.startsAt).getTime()).toBe(new Date(start).getTime());
    expect(new Date(body.endsAt).getTime()).toBe(new Date(end).getTime());
    keys.push(body.requestKey);
    items.push(row(assignment,false));
    return fulfill(route,{ok:true,replayed:false,assignmentId:assignment,status:'OFFERED'},201);
  });
  await page.setViewportSize({width:390,height:844});
  await visit(page);
  await expect(page.getByRole('heading',{name:'Назначения механиков'})).toBeVisible();
  const submit=page.getByRole('button',{name:'Предложить назначение'});
  await expect(submit).toBeDisabled();
  await page.getByLabel('UUID заказ-наряда').fill(order);
  await page.getByLabel('UUID механика',{exact:true}).fill(mechanic);
  await page.getByLabel('Начало назначения (ISO с поясом)').fill(start);
  await page.getByLabel('Конец назначения (ISO с поясом)').fill(end);
  await expect(submit).toBeEnabled();
  await submit.focus();
  await page.keyboard.press('Enter');
  const queue=page.getByLabel('Текущая очередь назначений');
  await expect(queue.getByRole('row')).toHaveCount(2);
  await expect(queue.getByRole('table')).toContainText(order);
  await expect(queue.getByRole('table')).toContainText('Ожидает ответа');
  await expect(queue.getByRole('button',{name:'Принять назначение'})).toHaveCount(0);
  await expect(queue.getByRole('link',{name:'Перейти к работам'}))
    .toHaveAttribute('href',new RegExp('/staff/auto-execution\\?.*order='+order));
  expect(keys).toHaveLength(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.screenshot({path:'test-results/auto-assignments-390-light.png'});
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:'test-results/auto-assignments-1280-dark.png'});
});

test('механик принимает и отклоняет только свои предложения',async({page})=>{
  const items:Row[]=[row(assignment,true),row(second,true)];
  const actions:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>
    fulfill(route,{csrfToken:'synthetic-csrf'}));
  await page.route(base,route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='GET')return fulfill(route,result(items,false));
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    const key=req.postDataJSON().requestKey;
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    if(path.endsWith('/accept')){
      actions.push('ACCEPTED');
      items[0]={...items[0],status:'ACCEPTED',acceptedAt:new Date().toISOString(),canRespond:false};
      return fulfill(route,{ok:true,replayed:false,status:'ACCEPTED'},201);
    }
    expect(path.endsWith('/decline')).toBeTruthy();
    actions.push('DECLINED');items.pop();
    return fulfill(route,{ok:true,replayed:false,status:'DECLINED'},201);
  });
  await visit(page);
  await expect(page.getByText('Действующие предложения и принятые задания')).toBeVisible();
  await expect(page.getByRole('button',{name:'Предложить назначение'})).toHaveCount(0);
  await page.getByRole('button',{name:'Принять назначение'}).first().click();
  await expect(page.getByText('Принято',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Отклонить назначение'}).click();
  await expect(page.getByRole('row')).toHaveCount(2);
  expect(actions).toEqual(['ACCEPTED','DECLINED']);
});

test('неизвестный результат POST повторяет исходный requestKey и payload',async({page})=>{
  const items:Row[]=[];
  const seen:string[]=[];
  let attempts=0;
  await page.route('**/api/v1/auth/csrf',route=>
    fulfill(route,{csrfToken:'synthetic-csrf'}));
  await page.route(base,route=>{
    const request=route.request();
    if(request.method()==='GET')return fulfill(route,result(items,true));
    seen.push(request.postData()||'');
    attempts++;
    if(attempts===1){
      items.push(row(assignment,false));
      return route.abort('failed');
    }
    return fulfill(route,{ok:true,replayed:true,assignmentId:assignment,status:'OFFERED'});
  });
  await visit(page);
  await page.getByLabel('UUID заказ-наряда').fill(order);
  await page.getByLabel('UUID механика',{exact:true}).fill(mechanic);
  await page.getByLabel('Начало назначения (ISO с поясом)').fill(start);
  await page.getByLabel('Конец назначения (ISO с поясом)').fill(end);
  await page.getByRole('button',{name:'Предложить назначение'}).click();
  await expect(page.getByRole('button',{name:'Повторить тот же запрос'})).toBeVisible();
  await expect(page.getByLabel('Текущая очередь назначений')).toHaveCount(0);
  await page.getByRole('button',{name:'Повторить тот же запрос'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  expect(seen).toHaveLength(2);
  expect(seen[0]).toBe(seen[1]);
  expect(JSON.parse(seen[0]).requestKey).toMatch(/^[0-9a-f-]{36}$/);
  expect(items).toHaveLength(1);
});

test('403 и повреждённый ответ немедленно очищают очередь, смена филиала безопасна',async({page})=>{
  let mode:'ready'|'denied'|'bad'='ready';
  await page.route(base,route=>{
    if(mode==='denied')return fulfill(route,{code:'FORBIDDEN'},403);
    const data=result([row(assignment,true)],false);
    if(mode==='bad')return fulfill(route,{...data,releaseAllowed:true});
    return fulfill(route,data);
  });
  await visit(page);
  await expect(page.getByRole('table')).toContainText(order);
  mode='denied';
  await page.getByRole('button',{name:'Обновить назначения'}).click();
  await expect(page.getByRole('alert')).toContainText('доступ запрещён');
  await expect(page.getByRole('table')).toHaveCount(0);
  mode='bad';
  await page.getByRole('button',{name:'Обновить назначения'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить');
  await expect(page.getByRole('table')).toHaveCount(0);
  mode='ready';
  await page.getByRole('button',{name:'Обновить назначения'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByLabel('UUID филиала').fill('77777777-7777-4777-8777-777777777777');
  await expect(page.getByRole('table')).toHaveCount(0);
});
