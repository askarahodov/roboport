import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const actor='33333333-3333-4333-8333-333333333333';
const leadId='44444444-4444-4444-8444-444444444444';
const appointment='55555555-5555-4555-8555-555555555555';
const base='**/api/v1/dev/auto/organizations/**/crm-leads*';

function lead(stage:'NEW'|'CONTACTED'|'BOOKED'|'WON'|'LOST'='NEW',
  appointmentId:string|null=null,reasonCode:string|null=null,eventCount=1){
  return {id:leadId,ownerId:actor,createdById:actor,
    sourceCode:'WALK_IN',intakeKey:'66666666-6666-4666-8666-666666666666',
    createdAt:'2030-01-01T10:00:00+00:00',appointmentId,stage,reasonCode,
    stageChangedAt:'2030-01-01T10:00:00+00:00',eventCount};
}
async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
}

test('CRM full stage flow 390/1280 and dark, retry without PII',async({page})=>{
  let items:ReturnType<typeof lead>[]=[];
  const calls:Array<{path:string;body:Record<string,unknown>}>=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({csrfToken:'test-csrf'}),
  }));
  await page.route(base,async route=>{
    const req=route.request();
    if(req.method()==='GET'){
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,items})});
    }
    const body=req.postDataJSON() as Record<string,unknown>;
    calls.push({path:new URL(req.url()).pathname,body});
    if(req.url().endsWith('/booking')){
      items=[lead(items[0].stage,appointment,null,items[0].eventCount)];
    }else if(req.url().endsWith('/stage')){
      const next=String(body.stage) as 'CONTACTED'|'BOOKED'|'WON';
      items=[lead(next,items[0].appointmentId,null,items[0].eventCount+1)];
    }else{
      items=[lead()];
    }
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,replayed:false,lead:items[0]})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await expect(page.getByRole('heading',{name:'Обращения и стадии CRM (dev)'})).toBeVisible();
  await scope(page);
  await expect(page.getByText('Зарегистрированных обращений пока нет.')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID ответственного'}).fill(actor);
  await page.getByRole('button',{name:'Зарегистрировать обращение'}).click();
  await expect(page.getByRole('row',{name:/44444444/})).toContainText('NEW');
  await page.getByRole('button',{name:'Контакт обработан'}).click();
  await expect(page.getByRole('row',{name:/44444444/})).toContainText('CONTACTED');
  await expect(page.getByRole('button',{name:'Записан'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID записи '+leadId}).fill(appointment);
  await page.getByRole('button',{name:'Привязать запись'}).click();
  await expect(page.getByText('Запись: '+appointment)).toBeVisible();
  await page.getByRole('button',{name:'Записан'}).click();
  await expect(page.getByRole('row',{name:/44444444/})).toContainText('BOOKED');
  await page.getByRole('button',{name:'Успешно'}).click();
  await expect(page.getByRole('row',{name:/44444444/})).toContainText('WON');
  expect(calls.map(x=>x.path.endsWith('/booking')?'booking':
    x.path.endsWith('/stage')?'stage':'create')).toEqual([
      'create','stage','booking','stage','stage',
    ]);
  expect(calls[0].body).toMatchObject({ownerId:actor,sourceCode:'WALK_IN'});
  expect(typeof calls[0].body.requestKey).toBe('string');
  expect(typeof calls[0].body.intakeKey).toBe('string');
  expect(calls[2].body.appointmentId).toBe(appointment);
  expect(calls[3].body.stage).toBe('BOOKED');
  expect(JSON.stringify(items)).not.toContain('phone');
  expect(JSON.stringify(items)).not.toContain('email');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM branch switch and role revocation clear old leads',async({page})=>{
  let forbidden=false;
  await page.route(base,route=>route.fulfill({
    status:forbidden?403:200,contentType:'application/json',
    body:forbidden?'{"code":"FORBIDDEN"}':
      JSON.stringify({syntheticOnly:true,items:[lead()]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('77777777-7777-4777-8777-777777777777');
  await expect(page.getByRole('table')).toHaveCount(0);
  forbidden=true;
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('CRM refuses malformed lead response without exposing stale rows',async({page})=>{
  await page.route(base,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[{...lead(),eventCount:-1}]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить обращения');
  await expect(page.getByRole('table')).toHaveCount(0);
});
