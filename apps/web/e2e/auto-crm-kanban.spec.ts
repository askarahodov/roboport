import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const lead='33333333-3333-4333-8333-333333333333';
const link='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const manager='66666666-6666-4666-8666-666666666666';
const boardUrl='**/api/v1/dev/auto/organizations/**/crm/kanban';
const workflowUrl='**/api/v1/dev/auto/organizations/**/crm/workflow';
const leadUrl='**/api/v1/dev/auto/organizations/**/crm/leads**';

const defaultConfig=()=>({stages:[
  {code:'QUALIFIED',label:'Обработано',position:10,enabled:true,
    allowedFrom:['NEW'],lossReasons:[]},
  {code:'BOOKED',label:'Записан',position:20,enabled:true,
    allowedFrom:['QUALIFIED'],lossReasons:[]},
  {code:'LOST',label:'Отказ',position:30,enabled:true,
    allowedFrom:['NEW','QUALIFIED'],lossReasons:['PRICE','TIMING','NO_RESPONSE','OTHER']},
]});
const item=(stage:'NEW'|'QUALIFIED'|'BOOKED'|'LOST',assigned=owner)=>({
  leadId:lead,customerLinkId:link,ownerId:assigned,sourceCode:'WEB',
  stageCode:stage,createdAt:'2030-01-01T10:00:00Z',
});
const columns=(config:any)=>[
  {code:'NEW',label:'Новое',position:0,enabled:true,allowedFrom:[],lossReasons:[]},
  ...config.stages,
];
const csrf=async(page:import('@playwright/test').Page)=>{
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"crm-csrf"}',
  }));
};

test('CRM Kanban configures stage labels, assigns a lead and moves it with CSRF',async({page})=>{
  let config=defaultConfig(),version=0,ownerId=owner,stage:'NEW'|'QUALIFIED'='NEW';
  const assignmentRequests:any[]=[];
  await csrf(page);
  await page.route(workflowUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,version,canConfigure:true,config}),
    });
    const body=route.request().postDataJSON();
    expect(route.request().headers()['x-csrf-token']).toBe('crm-csrf');
    expect(body.expectedRevision).toBe(version);
    config=body.config;version++;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,version,config,replayed:false})});
  });
  await page.route(boardUrl,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,version,canConfigure:true,
      columns:columns(config),items:[item(stage,ownerId)],limit:200}),
  }));
  await page.route(leadUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[]}),
    });
    const body=route.request().postDataJSON();
    expect(route.request().headers()['x-csrf-token']).toBe('crm-csrf');
    if(route.request().url().endsWith('/assignment')){
      assignmentRequests.push(body);
      ownerId=body.assigneeId;
      if(assignmentRequests.length===1)return route.fulfill({status:503,body:'Unavailable'});
      expect(assignmentRequests[1]).toEqual(assignmentRequests[0]);
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({replayed:true,syntheticOnly:true,ownerId})});
    }
    stage=body.stageCode;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({replayed:false,syntheticOnly:true,status:stage})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-kanban');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('button',{name:'Загрузить канбан'}).click();
  await expect(page.getByRole('heading',{name:'Канбан обращений CRM (dev)'})).toBeVisible();
  await expect(page.getByText('Обращение: '+lead)).toBeVisible();
  await page.getByRole('textbox',{name:'Название LOST'}).fill('Не состоялось');
  await page.getByRole('button',{name:'Сохранить конфигурацию этапов'}).click();
  await expect(page.getByText('Конфигурация этапов сохранена.')).toBeVisible();
  await expect(page.getByText('Не состоялось',{exact:true})).toBeVisible();

  await page.getByRole('textbox',{name:'Ответственный '+lead}).fill(manager);
  await page.getByRole('button',{name:'Назначить'}).click();
  await expect(page.getByRole('button',{name:'Повторить операцию с тем же ключом'})).toBeVisible();
  await page.getByRole('button',{name:'Повторить операцию с тем же ключом'}).click();
  await expect(page.getByText('Ответственный назначен.')).toBeVisible();
  expect(assignmentRequests).toHaveLength(2);
  expect(assignmentRequests[0].requestKey).toBe(assignmentRequests[1].requestKey);
  await expect(page.getByText('Ответственный: '+manager)).toBeVisible();

  await page.getByRole('button',{name:'Изменить этап'}).click();
  await page.getByLabel('Новый этап '+lead).selectOption('QUALIFIED');
  await page.getByRole('button',{name:'Сохранить этап'}).click();
  await expect(page.getByText('Этап обращения обновлён.')).toBeVisible();
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM Kanban clears cards on revoked branch access',async({page})=>{
  let denied=false;
  await page.route(workflowUrl,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify({
      syntheticOnly:true,version:0,canConfigure:false,config:defaultConfig(),
    }),
  }));
  await page.route(boardUrl,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify({
      syntheticOnly:true,version:0,canConfigure:false,
      columns:columns(defaultConfig()),items:[item('NEW')],limit:200,
    }),
  }));
  await page.goto('/staff/auto-crm-kanban');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('button',{name:'Загрузить канбан'}).click();
  await expect(page.getByText('Обращение: '+lead)).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Загрузить канбан'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('Обращение: '+lead)).toHaveCount(0);
});

test('customer service consent is distinct from contact preference and never enables send',async({page})=>{
  let enabled=false,version=0;
  await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',
    body:'{"subjectId":"77777777-7777-4777-8777-777777777777"}'}));
  await page.route('**/api/v1/dev/account/preferences',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"inAppEnabled":true}',
  }));
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"crm-csrf"}',
  }));
  await page.route('**/api/v1/dev/account/crm-contact-preferences',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,sendAllowed:false,
      channels:{CALL:false,EMAIL:false,SMS:false},revisions:{CALL:0,EMAIL:0,SMS:0}}),
  }));
  await page.route('**/api/v1/dev/account/crm-contact-consent',route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,purposeCode:'SERVICE_UPDATE',
        legalConsentVerified:false,sendAllowed:false,
        channels:{CALL:enabled,EMAIL:false,SMS:false},
        revisions:{CALL:version,EMAIL:0,SMS:0}}),
    });
    const body=route.request().postDataJSON();
    expect(route.request().headers()['x-csrf-token']).toBe('crm-csrf');
    enabled=body.enabled;version++;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,purposeCode:'SERVICE_UPDATE',
        legalConsentVerified:false,sendAllowed:false,
        channels:{CALL:enabled,EMAIL:false,SMS:false},
        revisions:{CALL:version,EMAIL:0,SMS:0},replayed:false})});
  });
  await page.goto('/account/settings');
  const section=page.getByLabel('Тестовое согласие на сервисные уведомления CRM');
  await expect(section.getByText('Юридическое подтверждение: отсутствует. Отправка: запрещена.')).toBeVisible();
  await section.getByLabel('Звонок').check();
  await section.getByRole('button',{name:'Сохранить тестовый выбор: Звонок'}).click();
  await expect(section.getByLabel('Звонок')).toBeChecked();
  expect(enabled).toBe(true);
  await expect(section).toContainText('Юридическое подтверждение: отсутствует');
});
