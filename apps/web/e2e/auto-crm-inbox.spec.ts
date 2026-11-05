import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const link='33333333-3333-4333-8333-333333333333';
const lead='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const task='66666666-6666-4666-8666-666666666666';
const due=new Date(Date.now()-86400000).toISOString();
const row={taskId:task,leadId:lead,ownerId:owner,creatorId:owner,
  taskCode:'CALL_BACK',dueAt:due,createdAt:due,status:'OPEN',event:null};
const leadItem={leadId:lead,customerLinkId:link,ownerId:owner,
  sourceCode:'WEB',status:'NEW',syntheticOnly:true,events:[],createdAt:due};
const inbox='**/api/v1/dev/auto/organizations/**/crm/inbox';
const leads='**/api/v1/dev/auto/organizations/**/crm/leads**';
const tasks='**/api/v1/dev/auto/organizations/**/crm/leads/*/tasks**';

async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
}

test('staff CRM inbox: overdue task opens lead outside limited list, DONE refreshes queue',async({page})=>{
  let done=false;let detailReads=0;let posted=0;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(leads,route=>{
    if(route.request().url().endsWith('/'+lead)){detailReads++;return route.fulfill({
      status:200,contentType:'application/json',body:JSON.stringify(leadItem),
    });}
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[]})});
  });
  await page.route(inbox,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:done?[]:[row],nextCursor:null}),
  }));
  await page.route(tasks,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[done?{...row,status:'DONE',
        event:{id:'77777777-7777-4777-8777-777777777777',actorId:owner,
          action:'DONE',createdAt:new Date().toISOString()}}:row]}),
    });
    posted++;
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
    expect(route.request().postDataJSON().action).toBe('DONE');
    done=true;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({...row,status:'DONE',event:{
        id:'77777777-7777-4777-8777-777777777777',actorId:owner,
        action:'DONE',createdAt:new Date().toISOString()}}),
    });
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const queue=page.getByLabel('Очередь CRM');
  await expect(queue.getByText('Просрочена',{exact:false})).toBeVisible();
  await queue.getByRole('button',{name:'Открыть обращение'}).click();
  await expect(page.getByText('Текущий этап: Новое.',{exact:false})).toBeVisible();
  expect(detailReads).toBe(1);
  await page.getByRole('button',{name:'Выполнено'}).click();
  await expect(queue.getByText('Открытых задач нет.')).toBeVisible();
  expect(posted).toBe(1);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM inbox denies revoked access and wipes previous queue on scope switch',async({page})=>{
  let denied=false;
  await page.route(leads,route=>route.fulfill({status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[]})}));
  await page.route(inbox,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify({syntheticOnly:true,items:[row],nextCursor:null}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByLabel('Очередь CRM').getByRole('button',
    {name:'Открыть обращение'})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить очередь'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('button',{name:'Открыть обращение'})).toHaveCount(0);
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('88888888-8888-4888-8888-888888888888');
  await expect(page.getByRole('button',{name:'Открыть обращение'})).toHaveCount(0);
});

test('CRM inbox malformed or oversized response fails closed',async({page})=>{
  await page.route(leads,route=>route.fulfill({status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[]})}));
  await page.route(inbox,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[{...row,leadId:'not-a-uuid'}],nextCursor:null}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить очередь');
  await expect(page.getByRole('button',{name:'Открыть обращение'})).toHaveCount(0);
});

test('CRM inbox: next/previous pages, refresh resets cursor, 390/1280',async({page})=>{
  const taskIds=Array.from({length:27},(_,i)=>
    'aaaaaaaa-aaaa-4aaa-8aaa-'+String(i+1).padStart(12,'0'));
  const rows=taskIds.map((taskId,i)=>({...row,taskId,
    dueAt:new Date(Date.now()+(i+1)*86400000).toISOString()}));
  const seenQueries:string[]=[];
  await page.route(leads,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[]}),
  }));
  await page.route(inbox,route=>{
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    seenQueries.push(cursor??'FIRST');
    const items=cursor?rows.slice(25):rows.slice(0,25);
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items,
        nextCursor:cursor?null:'cursor-page-2'})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const queue=page.getByLabel('Очередь CRM');
  await expect(queue.locator('ol > li')).toHaveCount(25);
  await queue.getByRole('button',{name:'Следующая страница'}).click();
  await expect(queue.locator('ol > li')).toHaveCount(2);
  await expect(queue.getByRole('button',{name:'Предыдущая страница'})).toBeVisible();
  await queue.getByRole('button',{name:'Предыдущая страница'}).click();
  await expect(queue.locator('ol > li')).toHaveCount(25);
  await queue.getByRole('button',{name:'Обновить очередь'}).click();
  await expect(queue.getByRole('button',{name:'Предыдущая страница'})).toHaveCount(0);
  expect(seenQueries).toContain('cursor-page-2');
  expect(seenQueries.at(-1)).toBe('FIRST');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('CRM inbox: invalid nextCursor fails closed, previous tasks cleared on 403',async({page})=>{
  await page.route(leads,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[]}),
  }));
  let malformed=false;
  await page.route(inbox,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[row],
      nextCursor:malformed?'../../../malicious':null}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const queue=page.getByLabel('Очередь CRM');
  await expect(queue.getByRole('button',{name:'Открыть обращение'})).toBeVisible();
  malformed=true;
  await queue.getByRole('button',{name:'Обновить очередь'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить очередь');
  await expect(queue.getByRole('button',{name:'Открыть обращение'})).toHaveCount(0);
});
