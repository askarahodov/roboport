import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const actor='33333333-3333-4333-8333-333333333333';
const leadId='44444444-4444-4444-8444-444444444444';
const taskId='55555555-5555-4555-8555-555555555555';
const otherTask='66666666-6666-4666-8666-666666666666';
const crmPath='**/api/v1/dev/auto/organizations/**/crm/leads**';
const lead={
  leadId,customerLinkId:'77777777-7777-4777-8777-777777777777',
  ownerId:actor,sourceCode:'WEB',status:'NEW',createdAt:'2030-01-01T00:00:00Z',
  syntheticOnly:true,events:[],
};
type Task={id:string;ownerId:string;createdById:string;kind:string;dueDay:string;
  createdAt:string;status:'OPEN'|'DONE'|'CANCELED';reasonCode:string|null;
  closedById:string|null;closedAt:string|null};
const makeTask=(id:string,kind:string,dueDay:string):Task=>({
  id,ownerId:actor,createdById:actor,kind,dueDay,
  createdAt:'2030-01-01T00:00:00Z',status:'OPEN',
  reasonCode:null,closedById:null,closedAt:null,
});
async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('combobox',{name:'Обращение',exact:true}).selectOption(leadId);
}

test('CRM follow-ups: create, done, cancel with reason, 390/1280 dark',async({page})=>{
  let tasks:Task[]=[];
  const posts:Array<Record<string,unknown>>=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"test-csrf"}',
  }));
  await page.route(crmPath,route=>{
    const req=route.request();
    if(req.method()==='GET'){
      const isFollowup=req.url().includes('/followups');
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify(isFollowup?{syntheticOnly:true,items:tasks}:
          {syntheticOnly:true,items:[lead]})});
    }
    const body=req.postDataJSON() as Record<string,unknown>;
    posts.push(body);
    let updated:Task;
    if(req.url().endsWith('/followups')){
      updated=makeTask(posts.length===1?taskId:otherTask,
        String(body.kind),String(body.dueDay));
      tasks=[...tasks,updated];
    }else{
      const id=req.url().split('/followups/')[1].split('/')[0];
      const prev=tasks.find(t=>t.id===id);
      if(!prev)throw Error('task missing');
      updated={...prev,status:String(body.action) as 'DONE'|'CANCELED',
        reasonCode:body.reasonCode===null?null:String(body.reasonCode),
        closedById:actor,closedAt:'2030-01-02T00:00:00Z'};
      tasks=tasks.map(t=>t.id===id?updated:t);
    }
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,replayed:false,task:updated})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('heading',{name:'Задачи обратной связи (без отправки сообщений)'})).toBeVisible();
  await expect(page.getByText('Задач нет.')).toBeVisible();
  await page.getByLabel('Срок задачи').fill('2030-01-10');
  await page.getByRole('button',{name:'Добавить задачу'}).click();
  await expect(page.getByRole('row',{name:/CALLBACK_NOT_SENT/})).toContainText('OPEN');
  await page.getByRole('button',{name:'Выполнено'}).click();
  await expect(page.getByRole('row',{name:/CALLBACK_NOT_SENT/})).toContainText('DONE');
  await page.getByLabel('Вид задачи').selectOption('CHECK_DOCUMENTS');
  await page.getByRole('button',{name:'Добавить задачу'}).click();
  await expect(page.getByRole('row',{name:/CHECK_DOCUMENTS/})).toContainText('OPEN');
  await page.getByRole('button',{name:'Отменить задачу'}).click();
  await expect(page.getByRole('row',{name:/CHECK_DOCUMENTS/})).toContainText('CANCELED');
  expect(posts).toHaveLength(4);
  for(const item of posts)expect(typeof item.requestKey).toBe('string');
  expect(posts[0]).toMatchObject({kind:'CALLBACK_NOT_SENT',dueDay:'2030-01-10'});
  expect(posts[1]).toMatchObject({action:'DONE',reasonCode:null});
  expect(posts[3]).toMatchObject({action:'CANCELED',reasonCode:'NO_LONGER_NEEDED'});
  expect(JSON.stringify(tasks)).not.toContain('phone');
  expect(JSON.stringify(tasks)).not.toContain('email');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('branch switch and revoked CRM role never leave previous follow-up on screen',async({page})=>{
  await page.route(crmPath,route=>{
    const u=route.request().url();
    const allowed=u.includes('/locations/'+loc+'/');
    return route.fulfill({status:allowed?200:403,contentType:'application/json',
      body:allowed?JSON.stringify(u.includes('/followups')?{
        syntheticOnly:true,items:[makeTask(taskId,'CHECK_DOCUMENTS','2030-01-10')],
      }:{syntheticOnly:true,items:[lead]}):'{"code":"FORBIDDEN"}'});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('row',{name:/CHECK_DOCUMENTS/})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('88888888-8888-4888-8888-888888888888');
  await expect(page.getByRole('row',{name:/CHECK_DOCUMENTS/})).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
});

test('malformed follow-up JSON is rejected without rendering dangerous data',async({page})=>{
  await page.route(crmPath,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify(route.request().url().includes('/followups')?
      {syntheticOnly:true,items:[{...makeTask(taskId,'CHECK_DOCUMENTS','2030-01-10'),
        closedById:actor,status:'OPEN'}]}:{syntheticOnly:true,items:[lead]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить задачи');
  await expect(page.getByRole('row',{name:/CHECK_DOCUMENTS/})).toHaveCount(0);
});
