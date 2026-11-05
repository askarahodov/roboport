import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const lead='44444444-4444-4444-8444-444444444444';
const customerLink='55555555-5555-4555-8555-555555555555';
const owner='66666666-6666-4666-8666-666666666666';
const accountUrl='**/api/v1/dev/account/crm-contact-preferences';
const leadUrl='**/api/v1/dev/auto/organizations/**/crm/leads**';
const staffChoices='**/api/v1/dev/auto/organizations/**/crm/leads/*/contact-preferences';
const snapshot=(call:boolean,revision:number)=>({
  syntheticOnly:true,sendAllowed:false,
  channels:{CALL:call,EMAIL:false,SMS:false},
  revisions:{CALL:revision,EMAIL:0,SMS:0},
});
const staffSnap=(call:boolean)=>({
  syntheticOnly:true,sendAllowed:false,
  channels:{CALL:call,EMAIL:false,SMS:false},
});

test.beforeEach(async({page})=>{
  await page.route('**/api/v1/auth/me',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({subjectId:'77777777-7777-4777-8777-777777777777'}),
  }));
  await page.route('**/api/v1/dev/account/preferences',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({inAppEnabled:true}),
  }));
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"dev-csrf"}',
  }));
});

test('customer CRM choices: fail-closed default, exact retry, withdrawal and staff read',async({page})=>{
  let version=0,enabled=false,requests:unknown[]=[];
  let first=true;
  await page.route(accountUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify(snapshot(enabled,version)),
    });
    const input=route.request().postDataJSON();
    requests.push(input);
    expect(route.request().headers()['x-csrf-token']).toBe('dev-csrf');
    expect(input.channelCode).toBe('CALL');
    expect(input.expectedRevision).toBe(requests.length<=2?0:1);
    if(first){first=false;enabled=true;version=1;
      return route.fulfill({status:503,body:'Temporarily unavailable'});}
    if(requests.length===2)return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({...snapshot(enabled,version),replayed:true}),
    });
    enabled=false;version=2;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({...snapshot(enabled,version),replayed:false})});
  });
  await page.route('**/api/v1/dev/auto/organizations/**/crm/inbox**',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null}),
  }));
  await page.route(leadUrl,r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,nextCursor:null,items:[{
      leadId:lead,customerLinkId:customerLink,ownerId:owner,
      sourceCode:'WEB',status:'NEW',createdAt:new Date().toISOString(),
      syntheticOnly:true,events:[],
    }]}),
  }));
  await page.route(staffChoices,r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify(staffSnap(enabled)),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/settings');
  const section=page.getByLabel('Предпочтения способов связи CRM (dev)');
  await expect(section.getByLabel('Звонки')).not.toBeChecked();
  await expect(section.getByLabel('SMS')).not.toBeChecked();
  await expect(section.getByText('Это не юридическое согласие',{exact:false})).toBeVisible();
  await section.getByLabel('Звонки').check();
  await section.getByRole('button',{name:'Записать выбор: Звонки'}).click();
  const retry=section.getByRole('button',{name:'Повторить выбор с тем же ключом'});
  await expect(retry).toBeVisible();
  await expect(section.getByRole('button',{name:'Записать выбор: Звонки'})).toBeDisabled();
  await retry.click();
  await expect(retry).toHaveCount(0);
  await expect(section.getByLabel('Звонки')).toBeChecked();
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);

  // Staff sees only the aggregate flags for its currently authorized lead.
  await page.goto('/staff/auto-crm-leads');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByLabel('Обращение').selectOption(lead);
  const staff=page.getByLabel('Предпочтения связи клиента в CRM');
  await staff.getByRole('button',{name:'Проверить предпочтения клиента'}).click();
  await expect(staff).toContainText('Звонки: Клиент отметил предпочтение');
  await expect(staff).toContainText('Внешние сообщения: отправка запрещена.');
  await page.goto('/account/settings');
  await expect(section.getByLabel('Звонки')).toBeChecked();
  await section.getByLabel('Звонки').uncheck();
  await section.getByRole('button',{name:'Записать выбор: Звонки'}).click();
  await expect(section.getByLabel('Звонки')).not.toBeChecked();
  expect(requests).toHaveLength(3);
  const last=requests[2] as {requestKey:string;expectedRevision:number};
  expect(last.expectedRevision).toBe(1);
  expect(last.requestKey).not.toBe((requests[0] as {requestKey:string}).requestKey);
  await page.goto('/staff/auto-crm-leads');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByLabel('Обращение').selectOption(lead);
  await staff.getByRole('button',{name:'Проверить предпочтения клиента'}).click();
  await expect(staff).toContainText('Звонки: Клиент не разрешал');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('customer CRM preferences: invalid payload fails closed; revoked session clears form',async({page})=>{
  let state='invalid';
  await page.route(accountUrl,r=>r.fulfill({
    status:state==='revoked'?403:200,contentType:'application/json',
    body:state==='invalid'?
      '{"syntheticOnly":true,"sendAllowed":true,"channels":{"CALL":true}}':
      JSON.stringify(snapshot(false,0)),
  }));
  await page.goto('/account/settings');
  const section=page.getByLabel('Предпочтения способов связи CRM (dev)');
  await expect(section.getByText('Предпочтения связи недоступны.',{exact:false})).toBeVisible();
  await expect(section.getByRole('checkbox')).toHaveCount(0);
  state='valid';
  await section.getByRole('button',{name:'Повторить чтение'}).click();
  await expect(section.getByLabel('Звонки')).not.toBeChecked();
  state='revoked';
  await section.getByRole('button',{name:'Обновить предпочтения связи'}).click();
  await expect(section.getByRole('checkbox')).toHaveCount(0);
  await expect(section).toContainText('Нет доступа к предпочтениям связи');
});

test('staff CRM contact preferences revocation wipes prior lead and flags',async({page})=>{
  let forbidden=false;
  await page.route('**/api/v1/dev/auto/organizations/**/crm/inbox**',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null}),
  }));
  await page.route(leadUrl,r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,nextCursor:null,items:[{
      leadId:lead,customerLinkId:customerLink,ownerId:owner,
      sourceCode:'WEB',status:'NEW',createdAt:new Date().toISOString(),
      syntheticOnly:true,events:[],
    }]}),
  }));
  await page.route(staffChoices,r=>r.fulfill({
    status:forbidden?403:200,contentType:'application/json',
    body:forbidden?'{"code":"FORBIDDEN"}':JSON.stringify(staffSnap(true)),
  }));
  await page.goto('/staff/auto-crm-leads');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByLabel('Обращение').selectOption(lead);
  const staff=page.getByLabel('Предпочтения связи клиента в CRM');
  await staff.getByRole('button',{name:'Проверить предпочтения клиента'}).click();
  await expect(staff).toContainText('Клиент отметил предпочтение');
  forbidden=true;
  await staff.getByRole('button',{name:'Проверить предпочтения клиента'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
  await expect(page.getByText('Клиент отметил предпочтение')).toHaveCount(0);
});
