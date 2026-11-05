import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const link='33333333-3333-4333-8333-333333333333';
const lead='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const appointment='66666666-6666-4666-8666-666666666666';
const path='**/api/v1/dev/auto/organizations/**/crm/leads*';
const item=(stage:'NEW'|'QUALIFIED'|'BOOKED'|'LOST',events:unknown[]=[])=>({
  leadId:lead,customerLinkId:link,ownerId:owner,
  sourceCode:'WEB',status:stage,syntheticOnly:true,
  createdAt:'2030-01-01T10:00:00Z',events,
});
const event=(stage:'QUALIFIED'|'BOOKED'|'LOST',reason:string|null,booking:string|null)=>({
  id:'77777777-7777-4777-8777-777777777777',actorId:owner,stageCode:stage,
  reasonCode:reason,appointmentId:booking,createdAt:'2030-01-01T10:01:00Z',
});

async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
}

test('CRM creates and qualifies synthetic lead with CSRF, 390/1280 dark',async({page})=>{
  let created=false;
  let qualified=false;
  const posted:unknown[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(path,async route=>{
    if(route.request().method()==='GET'){
      const rows=created?[item(qualified?'QUALIFIED':'NEW',qualified?
        [event('QUALIFIED',null,null)]:[])]:[];
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,items:rows})});
    }
    const body=route.request().postDataJSON();
    posted.push(body);
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
    expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/i);
    if(route.request().url().endsWith('/stages')){
      qualified=true;
      expect(body.stageCode).toBe('QUALIFIED');
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({replayed:false,...item('QUALIFIED',[event('QUALIFIED',null,null)])})});
    }
    created=true;
    expect(body.customerLinkId).toBe(link);
    expect(body.sourceCode).toBe('WEB');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({replayed:false,...item('NEW')})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await expect(page.getByRole('heading',{name:'Обращения и этапы CRM'})).toBeVisible();
  await scope(page);
  await expect(page.getByText('Обращений пока нет.')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID подтверждённой связи'}).fill(link);
  await page.getByLabel('Источник').selectOption('WEB');
  await page.getByRole('button',{name:'Создать обращение'}).click();
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await page.getByLabel('Обращение').selectOption(lead);
  await expect(page.getByText('Текущий этап: NEW.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Сохранить этап'}).click();
  await expect(page.getByText('Текущий этап: QUALIFIED.',{exact:false})).toBeVisible();
  expect(posted).toHaveLength(2);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM revoked scope clears previous lead and denies staff operations',async({page})=>{
  let denied=false;
  await page.route(path,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify({
      syntheticOnly:true,items:[item('NEW')],
    }),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('88888888-8888-4888-8888-888888888888');
  denied=true;
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
  await expect(page.getByText('Текущий этап:',{exact:false})).toHaveCount(0);
});

test('CRM rejects malformed lead response without displaying untrusted owners',async({page})=>{
  await page.route(path,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[{
      ...item('NEW'),ownerId:'not-a-uuid',
    }]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить обращения.');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
});

test('CRM booked stage requires a scoped appointment UUID and qualified stage',async({page})=>{
  await page.route(path,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[item('QUALIFIED',
      [event('QUALIFIED',null,null)])]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('Обращение').selectOption(lead);
  await page.getByLabel('Этап').selectOption('BOOKED');
  await expect(page.getByRole('button',{name:'Сохранить этап'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID записи'}).fill(appointment);
  await expect(page.getByRole('button',{name:'Сохранить этап'})).toBeEnabled();
});
