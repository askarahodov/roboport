import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const link='33333333-3333-4333-8333-333333333333';
const lead='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const task='66666666-6666-4666-8666-666666666666';
const leadBase='**/api/v1/dev/auto/organizations/**/crm/leads**';
const taskBase='**/api/v1/dev/auto/organizations/**/crm/leads/*/tasks**';
const leadItem={leadId:lead,customerLinkId:link,ownerId:owner,
  sourceCode:'WEB',status:'NEW',syntheticOnly:true,events:[],
  createdAt:'2030-01-01T10:00:00Z'};
const due=new Date(Date.now()+8*86400000);
const taskItem={taskId:task,ownerId:owner,creatorId:owner,
  taskCode:'CALL_BACK',dueAt:due.toISOString(),
  createdAt:new Date().toISOString(),status:'OPEN',event:null};
const localDue=()=>{
  const d=new Date(Date.now()+8*86400000);
  return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);
};
async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await page.getByLabel('Обращение').selectOption(lead);
}
function leadRoute(page:import('@playwright/test').Page){
  return page.route(leadBase,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[leadItem]}),
  }));
}

test('follow-up: create then DONE with CSRF, 390/1280, dark',async({page})=>{
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"demo-csrf"}',
  }));
  await leadRoute(page);
  let created=false;let done=false;
  const posts:any[]=[];
  await page.route(taskBase,route=>{
    const req=route.request();
    if(req.method()==='GET'){
      const item=done?{...taskItem,status:'DONE',event:{
        id:'77777777-7777-4777-8777-777777777777',actorId:owner,
        action:'DONE',createdAt:new Date().toISOString(),
      }}:taskItem;
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,items:created?[item]:[]})});
    }
    expect(req.headers()['x-csrf-token']).toBe('demo-csrf');
    const body=req.postDataJSON();
    expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/i);
    posts.push(body);
    if(req.url().endsWith('/events')){
      expect(body.action).toBe('DONE');
      done=true;
    }else{
      expect(body.taskCode).toBe('CALL_BACK');
      expect(new Date(body.dueAt).getTime()).toBeGreaterThan(Date.now());
      created=true;
    }
    const item=done?{...taskItem,status:'DONE',event:{
      id:'77777777-7777-4777-8777-777777777777',actorId:owner,
      action:'DONE',createdAt:new Date().toISOString(),
    }}:taskItem;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({...item,replayed:false})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByText('Задач пока нет.')).toBeVisible();
  await page.getByLabel('Тип задачи').selectOption('CALL_BACK');
  await page.getByLabel('Срок задачи').fill(localDue());
  await page.getByRole('button',{name:'Создать задачу'}).click();
  await expect(page.getByRole('button',{name:'Выполнено'})).toBeVisible();
  await page.getByRole('button',{name:'Выполнено'}).click();
  const finishedTask=page.locator('[aria-label="Задачи по обращению"] ol > li').first();
  await expect(finishedTask).toContainText('Связаться позже');
  await expect(finishedTask).toContainText('Выполнена');
  await expect(finishedTask).not.toContainText('CALL_BACK');
  await expect(page.getByRole('button',{name:'Выполнено'})).toHaveCount(0);
  expect(posts).toHaveLength(2);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('follow-up list clears on branch switch and 403',async({page})=>{
  await leadRoute(page);
  let denied=false;
  await page.route(taskBase,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify({
      syntheticOnly:true,items:[taskItem],
    }),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('button',{name:'Выполнено'})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить задачи'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('button',{name:'Выполнено'})).toHaveCount(0);
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('88888888-8888-4888-8888-888888888888');
  await expect(page.getByText('CALL_BACK',{exact:false})).toHaveCount(0);
});

test('follow-up malformed status/event response is fail closed',async({page})=>{
  await leadRoute(page);
  await page.route(taskBase,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[{
      ...taskItem,status:'DONE',event:null,
    }]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить задачи');
  await expect(page.getByRole('button',{name:'Создать задачу'})).toHaveCount(0);
});
