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
  return {syntheticOnly:true,releaseAllowed:false,canOffer,
    organizationId:org,locationId:loc,items:rows};
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
  await page.getByLabel('Начало назначения (ISO с поясом)').fill(start.slice(0,-1));
  await page.getByLabel('Конец назначения (ISO с поясом)').fill(end);
  await expect(submit).toBeDisabled();
  // toISOString() includes fractional seconds. They remain legal only with Z/offset.
  await page.getByLabel('Начало назначения (ISO с поясом)').fill(start);
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


test('MANAGER отзывает застрявший OFFERED, повторяет тот же ключ и скрывает данные при 403',
  async({page},info)=>{
    let present=true;
    let attempt=0;
    let denied=false;
    const bodies:string[]=[];
    await page.route('**/api/v1/auth/csrf',route=>
      fulfill(route,{csrfToken:'synthetic-csrf'}));
    await page.route(base,route=>{
      const request=route.request(),pathname=new URL(request.url()).pathname;
      if(denied)return fulfill(route,{code:'FORBIDDEN'},403);
      if(request.method()==='GET'){
        // Отменённая смена скрывает старый offer из обычной очереди,
        // но MANAGER сохраняет доступ к отдельному списку на отзыв.
        return fulfill(route,{
          ...result([],true),
          withdrawable:present?[{
            assignmentId:assignment,workOrderId:order,
            mechanicId:mechanic,startsAt:start,endsAt:end,status:'OFFERED',
          }]:[],
        });
      }
      expect(pathname).toContain('/assignments/'+assignment+'/withdraw');
      expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
      bodies.push(request.postData()??'');
      expect(request.postDataJSON().requestKey).toMatch(/^[0-9a-f-]{36}$/);
      attempt++;
      if(attempt===1){
        present=false;
        return route.abort('failed');
      }
      return fulfill(route,{ok:true,replayed:true,status:'WITHDRAWN'});
    });
    await page.setViewportSize({width:390,height:844});
    await visit(page);
    const block=page.getByRole('region',{name:'Отзыв незавершённых предложений'});
    await expect(block).toContainText(order);
    await expect(block).toContainText(mechanic);
    await expect(page.getByLabel('Текущая очередь назначений')).toContainText(
      'Для ваших прав действующих назначений нет.');
    const button=block.getByRole('button',{name:'Отозвать предложение '+assignment});
    await expect(button).toBeEnabled();
    await page.screenshot({path:info.outputPath('withdraw-390-stale-offer.png'),
      fullPage:true,animations:'disabled'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(390);
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button',{name:'Повторить тот же запрос'})).toBeVisible();
    await expect(block).toHaveCount(0);
    await page.getByRole('button',{name:'Повторить тот же запрос'}).click();
    await expect(block.getByText('Непринятых предложений на этой странице нет.')).toBeVisible();
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toBe(bodies[1]);

    await page.setViewportSize({width:1280,height:900});
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
    await expect(page.locator('main.app.dark')).toBeVisible();
    await page.screenshot({path:info.outputPath('withdraw-1280-after-recall.png'),
      fullPage:true,animations:'disabled'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(1280);
    denied=true;
    await page.getByRole('button',{name:'Обновить назначения'}).click();
    await expect(page.getByRole('alert')).toContainText('доступ запрещён');
    await expect(block).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Отозвать предложение '+assignment}))
      .toHaveCount(0);
  });

test('MECHANIC не получает список чужих предложений на отзыв',async({page})=>{
  await page.route(base,route=>fulfill(route,{
    ...result([row(assignment,true)],false),withdrawable:[],
  }));
  await visit(page);
  await expect(page.getByLabel('Текущая очередь назначений')).toContainText(order);
  await expect(page.getByRole('region',{name:'Отзыв незавершённых предложений'}))
    .toHaveCount(0);
  await expect(page.getByRole('button',{name:'Отозвать предложение '+assignment}))
    .toHaveCount(0);
});


test('MANAGER просматривает все 101 зависшее предложение постранично без 409',
  async({page},testInfo)=>{
    const all=Array.from({length:101},(_,i)=>({
      assignmentId:'00000000-0000-4000-8000-'+String(i+1).padStart(12,'0'),
      workOrderId:order,mechanicId:mechanic,
      startsAt:start,endsAt:end,status:'OFFERED' as const,
    }));
    const queried:number[]=[];
    await page.route(base,route=>{
      const url=new URL(route.request().url());
      expect(route.request().method()).toBe('GET');
      const offset=Number(url.searchParams.get('withdrawPage')??0);
      queried.push(offset);
      return fulfill(route,{
        ...result([],true),withdrawPage:offset,
        withdrawable:all.slice(offset*50,offset*50+50),
        withdrawHasMore:(offset+1)*50<all.length,
      });
    });
    await page.setViewportSize({width:390,height:844});
    await visit(page);
    const block=page.getByRole('region',{name:'Отзыв незавершённых предложений'});
    await expect(block.getByRole('row')).toHaveCount(51);
    await expect(block.getByText('Страница отзыва: 1.')).toBeVisible();
    await page.getByRole('button',{name:'Следующие предложения для отзыва'}).click();
    await expect(block.getByRole('row')).toHaveCount(51);
    await expect(block.getByText('Страница отзыва: 2.')).toBeVisible();
    await page.getByRole('button',{name:'Следующие предложения для отзыва'}).click();
    await expect(block.getByRole('row')).toHaveCount(2);
    await expect(block.getByText('Страница отзыва: 3.')).toBeVisible();
    await expect(page.getByRole('button',{name:'Следующие предложения для отзыва'}))
      .toHaveCount(0);
    await page.screenshot({path:testInfo.outputPath('withdraw-390-page3.png'),
      fullPage:true,animations:'disabled'});
    await page.getByRole('button',{name:'Предыдущая страница отзыва'}).click();
    await expect(block.getByText('Страница отзыва: 2.')).toBeVisible();
    expect(queried).toContain(0);
    expect(queried).toContain(1);
    expect(queried).toContain(2);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(390);
  });
