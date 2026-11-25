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
  await expect(page.getByRole('heading',{name:/^Не состоялось \(\d+\)$/})).toBeVisible();

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
  const confirmed=page.waitForResponse(response=>
    response.url().endsWith('/api/v1/dev/account/crm-contact-consent')&&response.request().method()==='POST');
  await section.getByRole('button',{name:'Сохранить тестовый выбор: Звонок'}).click();
  expect((await confirmed).status()).toBe(201);
  await expect(section).toContainText('Тестовый выбор записан. Реальная отправка остаётся выключенной.');
  await expect(section.getByLabel('Звонок')).toBeChecked();
  expect(enabled).toBe(true);
  await expect(section).toContainText('Юридическое подтверждение: отсутствует');
});


test('CRM Kanban selects the only enabled transition and does not request an appointment',async({page})=>{
  const config=defaultConfig();
  config.stages.find((s:any)=>s.code==='QUALIFIED')!.enabled=false;
  config.stages.find((s:any)=>s.code==='BOOKED')!.enabled=false;
  let savedStage:string|null=null;
  await page.route(workflowUrl,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,version:1,canConfigure:true,config}),
  }));
  await page.route(boardUrl,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,version:1,canConfigure:true,
      columns:columns(config),items:[item('NEW')],limit:200}),
  }));
  await page.route(leadUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',body:'{"syntheticOnly":true,"items":[]}',
    });
    const body=route.request().postDataJSON();
    savedStage=body.stageCode;
    expect(body.appointmentId).toBeNull();
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({replayed:false,syntheticOnly:true,status:savedStage})});
  });
  await csrf(page);
  await page.goto('/staff/auto-crm-kanban');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('button',{name:'Загрузить канбан'}).click();
  await page.getByRole('button',{name:'Изменить этап'}).click();
  const select=page.getByLabel('Новый этап '+lead);
  await expect(select).toHaveValue('LOST');
  await expect(page.getByLabel('UUID записи '+lead)).toHaveCount(0);
  await page.getByRole('button',{name:'Сохранить этап'}).click();
  await expect(page.getByText('Этап обращения обновлён.')).toBeVisible();
  expect(savedStage).toBe('LOST');

  config.stages.forEach((stage:any)=>{stage.enabled=false;});
  await page.getByRole('button',{name:'Загрузить канбан'}).click();
  await expect(page.getByText('Нет доступных переходов.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Изменить этап'})).toBeDisabled();
});


test('Kanban creates repeat deal in NEW without modifying terminal card',async({page})=>{
  const next='88888888-8888-4888-8888-888888888888';
  const config=defaultConfig();
  let created=false;
  const posts:any[]=[];
  await csrf(page);
  await page.route(workflowUrl,route=>route.fulfill({status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,version:0,canConfigure:true,config})}));
  await page.route(boardUrl,route=>route.fulfill({status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,version:0,canConfigure:true,limit:200,
      columns:columns(config),items:[{...item('LOST'),visitNumber:1},
        ...(created?[{...item('NEW'),leadId:next,visitNumber:2}]:[])]})}));
  await page.route(leadUrl,route=>{
    posts.push(route.request().postDataJSON());
    expect(posts.at(-1)).toEqual(posts[0]);
    created=true;
    if(posts.length===1)return route.abort('failed');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,replayed:true,leadId:next,visitNumber:2,status:'NEW'})});
  });
  await page.goto('/staff/auto-crm-kanban');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByRole('button',{name:'Загрузить канбан'}).click();
  await expect(page.getByRole('region',{name:'Этап LOST'})).toContainText('Цикл обращения №1');
  await page.getByRole('button',{name:'Создать повторное обращение'}).click();
  await page.getByRole('button',{name:'Повторить операцию с тем же ключом'}).click();
  await expect(page.getByRole('region',{name:'Этап NEW'})).toContainText('Цикл обращения №2');
  await expect(page.getByRole('region',{name:'Этап LOST'})).toContainText('Цикл обращения №1');
  expect(posts).toHaveLength(2);
  expect(posts[0]).toMatchObject({customerLinkId:link,sourceCode:'WEB'});
});
