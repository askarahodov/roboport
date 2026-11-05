import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const link='33333333-3333-4333-8333-333333333333';
const lead='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const order='66666666-6666-4666-8666-666666666666';
const appointment='77777777-7777-4777-8777-777777777777';
const task='88888888-8888-4888-8888-888888888888';
const due=new Date(Date.now()+12*86400000);
const localDue=()=>new Date(due.getTime()-due.getTimezoneOffset()*60000)
  .toISOString().slice(0,16);
const leadBase='**/api/v1/dev/auto/organizations/**/crm/leads**';
const taskBase='**/api/v1/dev/auto/organizations/**/crm/leads/*/tasks**';
const serviceBase='**/api/v1/dev/auto/organizations/**/crm/leads/*/service-review';
const leadItem={
  leadId:lead,customerLinkId:link,ownerId:owner,sourceCode:'WEB',status:'BOOKED',
  createdAt:'2030-01-01T10:00:00Z',syntheticOnly:true,events:[
    {id:'99999999-9999-4999-8999-999999999999',actorId:owner,
      stageCode:'QUALIFIED',reasonCode:null,appointmentId:null,
      createdAt:'2030-01-01T10:10:00Z'},
    {id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',actorId:owner,
      stageCode:'BOOKED',reasonCode:null,appointmentId:appointment,
      createdAt:'2030-01-01T10:20:00Z'},
  ],
};
const item={
  taskId:task,ownerId:owner,creatorId:owner,taskCode:'SERVICE_REVIEW',
  originWorkOrderId:order,dueAt:due.toISOString(),
  createdAt:new Date().toISOString(),status:'OPEN',event:null,
};

async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await page.getByLabel('Обращение').selectOption(lead);
}

test.beforeEach(async({page})=>{
  await page.route('**/api/v1/dev/auto/organizations/**/crm/inbox**',
    route=>route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null})}));
  await page.route(leadBase,route=>route.fulfill({status:200,
    contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[leadItem],nextCursor:null})}));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"demo-csrf"}',
  }));
});

test('post-service review: QC-backed manual task, lost response, exact-key replay, DONE',async({page})=>{
  let created=false;let done=false;
  const posts:any[]=[];
  await page.route(taskBase,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:
        created?[{...item,status:done?'DONE':'OPEN',
          event:done?{id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',actorId:owner,
            action:'DONE',createdAt:new Date().toISOString()}:null}]:[]}),
    });
    done=true;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({...item,status:'DONE',
        event:{id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',actorId:owner,
          action:'DONE',createdAt:new Date().toISOString()}})});
  });
  await page.route(serviceBase,route=>{
    const body=route.request().postDataJSON();
    posts.push(body);
    expect(route.request().headers()['x-csrf-token']).toBe('demo-csrf');
    expect(body.workOrderId).toBe(order);
    if(posts.length===1){created=true;return route.abort('failed');}
    if(posts.length===2)return route.fulfill({status:503,body:'Unavailable'});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({...item,replayed:true})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const create=page.getByRole('button',{name:'Запланировать контроль после ремонта'});
  await expect(create).toBeDisabled();
  await page.getByLabel('UUID выполненного заказ-наряда').fill(order);
  await page.getByLabel('Срок контроля после ремонта').fill(localDue());
  await expect(create).toBeEnabled();
  await create.click();
  const retry=page.getByRole('button',{name:'Повторить запрос с тем же ключом'});
  await expect(retry).toBeVisible();
  // Unknown POST outcome hides the new-action form: only exact-key replay remains.
  await expect(create).toHaveCount(0);
  await retry.click();
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(retry).toHaveCount(0);
  await expect(page.getByText('Наряд: '+order)).toBeVisible();
  expect(posts).toHaveLength(3);
  expect(posts[0]).toEqual(posts[1]);
  expect(posts[1]).toEqual(posts[2]);
  await page.getByRole('button',{name:'Выполнено'}).click();
  const finished=page.locator('[aria-label="Задачи по обращению"] ol > li').first();
  await expect(finished).toContainText('Выполнена');
  await expect(finished).toContainText(order);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('service-review rejects not finished QC, revoked 403 and invalid payload',async({page})=>{
  let outcome=409;let postCount=0;
  await page.route(taskBase,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[]}),
  }));
  await page.route(serviceBase,route=>{
    postCount++;
    return route.fulfill({status:outcome,contentType:'application/json',
      body:JSON.stringify({code:outcome===403?'FORBIDDEN':'CONFLICT'})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const create=page.getByRole('button',{name:'Запланировать контроль после ремонта'});
  await expect(create).toBeDisabled();
  await page.getByLabel('UUID выполненного заказ-наряда').fill('not-a-uuid');
  await page.getByLabel('Срок контроля после ремонта').fill(localDue());
  await expect(create).toBeDisabled();
  expect(postCount).toBe(0);
  await page.getByLabel('UUID выполненного заказ-наряда').fill(order);
  await create.click();
  await expect(page.getByRole('status')).toContainText('Конфликт задачи');
  outcome=403;
  await create.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(create).toHaveCount(0);
  expect(postCount).toBe(2);
});
