import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const lead='33333333-3333-4333-8333-333333333333';
const link='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const consentUrl='**/api/v1/dev/account/crm-contact-consent';
const leadsUrl='**/api/v1/dev/auto/organizations/**/crm/leads**';
const mockUrl='**/api/v1/dev/auto/organizations/**/crm/leads/*/mock-messages';
const consentLeadUrl='**/api/v1/dev/auto/organizations/**/crm/leads/*/contact-consent';

test.beforeEach(async({page})=>{
  await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',
    body:'{"subjectId":"77777777-7777-4777-8777-777777777777"}'}));
  await page.route('**/api/v1/dev/account/preferences',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"inAppEnabled":true}',
  }));
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"crm-csrf"}',
  }));
});

test('service consent is separate, revisioned and never permits external sending',async({page})=>{
  let enabled=false,version=0;
  await page.route('**/api/v1/dev/account/crm-contact-preferences',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,sendAllowed:false,
      channels:{CALL:false,EMAIL:false,SMS:false},revisions:{CALL:0,EMAIL:0,SMS:0}}),
  }));
  await page.route(consentUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,purposeCode:'SERVICE_UPDATE',
        legalConsentVerified:false,sendAllowed:false,
        channels:{CALL:enabled,EMAIL:false,SMS:false},
        revisions:{CALL:version,EMAIL:0,SMS:0}}),
    });
    const body=route.request().postDataJSON();
    expect(route.request().headers()['x-csrf-token']).toBe('crm-csrf');
    expect(body.channelCode).toBe('CALL');
    expect(body.expectedRevision).toBe(version);
    enabled=body.enabled;version++;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,purposeCode:'SERVICE_UPDATE',
        legalConsentVerified:false,sendAllowed:false,
        channels:{CALL:enabled,EMAIL:false,SMS:false},
        revisions:{CALL:version,EMAIL:0,SMS:0},replayed:false})});
  });
  await page.goto('/account/settings');
  const section=page.getByLabel('Тестовое согласие на сервисные уведомления CRM');
  await expect(section).toContainText('не юридически подтверждённое согласие');
  await expect(section.getByLabel('Звонок')).not.toBeChecked();
  await section.getByLabel('Звонок').check();
  const firstSave=page.waitForResponse(response=>
    response.url().endsWith('/api/v1/dev/account/crm-contact-consent')&&
    response.request().method()==='POST');
  await section.getByRole('button',{name:'Сохранить тестовый выбор: Звонок'}).click();
  const firstSaveResponse=await firstSave;
  expect(firstSaveResponse.status()).toBe(201);
  await expect(section.getByLabel('Звонок')).toBeChecked();
  await expect(section).toContainText('Тестовый выбор записан. Реальная отправка остаётся выключенной.');
  await expect(section).toContainText('Юридическое подтверждение: отсутствует. Отправка: запрещена.');
  expect(enabled).toBe(true);
  await section.getByLabel('Звонок').uncheck();
  const revokeSave=page.waitForResponse(response=>
    response.url().endsWith('/api/v1/dev/account/crm-contact-consent')&&
    response.request().method()==='POST');
  await section.getByRole('button',{name:'Сохранить тестовый выбор: Звонок'}).click();
  const revokeSaveResponse=await revokeSave;
  expect(revokeSaveResponse.status()).toBe(201);
  await expect(section.getByLabel('Звонок')).not.toBeChecked();
  expect(enabled).toBe(false);
});

test('staff creates an inert mock once with same-key retry and suppresses it after unsubscribe',async({page})=>{
  let consent=true;
  let outbox:any[]=[];
  const posts:any[]=[];
  await page.route(leadsUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,nextCursor:null,items:[{
        syntheticOnly:true,leadId:lead,customerLinkId:link,ownerId:owner,sourceCode:'WEB',
        status:'NEW',createdAt:'2030-01-01T10:00:00Z',events:[],
      }]}),
    });
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({replayed:false,syntheticOnly:true,status:'MOCK_ONLY',
        sendAllowed:false,messageId:'88888888-8888-4888-8888-888888888888'})});
  });
  await page.route('**/api/v1/dev/auto/organizations/**/crm/inbox**',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null}),
  }));
  await page.route('**/api/v1/dev/auto/organizations/**/crm/leads/*/tasks**',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null}),
  }));
  await page.route('**/api/v1/dev/auto/organizations/**/crm/leads/*/contact-preferences',r=>r.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,sendAllowed:false,
      channels:{CALL:false,EMAIL:false,SMS:false}}),
  }));
  await page.route(consentLeadUrl,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,purposeCode:'SERVICE_UPDATE',
      legalConsentVerified:false,sendAllowed:false,
      channels:{CALL:consent,EMAIL:false,SMS:false}}),
  }));
  await page.route(mockUrl,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,sendAllowed:false,
        legalConsentVerified:false,items:outbox}),
    });
    const body=route.request().postDataJSON();
    posts.push(body);
    expect(route.request().headers()['x-csrf-token']).toBe('crm-csrf');
    if(posts.length===1){
      outbox=[{messageId:'88888888-8888-4888-8888-888888888888',
        channelCode:body.channelCode,templateCode:body.templateCode,
        status:'MOCK_ONLY',createdAt:'2030-01-01T10:01:00Z'}];
      return route.fulfill({status:503,body:'Unavailable'});
    }
    expect(body).toEqual(posts[0]);
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({replayed:true,syntheticOnly:true,sendAllowed:false,
        messageId:'88888888-8888-4888-8888-888888888888',
        channelCode:body.channelCode,templateCode:body.templateCode,status:'MOCK_ONLY',
        createdAt:'2030-01-01T10:01:00Z'})});
  });
  await page.goto('/staff/auto-crm-leads');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByLabel('Обращение').selectOption(lead);
  const panel=page.getByLabel('Инертная очередь тестовых CRM уведомлений');
  await panel.getByRole('button',{name:'Проверить согласие и очередь'}).click();
  await expect(panel).toContainText('Юридически проверенное согласие: нет');
  await panel.getByRole('button',{name:'Создать инертный макет'}).click();
  const retry=panel.getByRole('button',{name:'Повторить с тем же ключом'});
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(panel).toContainText('Добавлена только инертная запись MOCK_ONLY');
  expect(posts).toHaveLength(2);
  expect(posts[0].requestKey).toBe(posts[1].requestKey);
  expect(JSON.stringify(posts[0])).not.toContain('phone');
  expect(JSON.stringify(posts[0])).not.toContain('email');
  await expect(panel).toContainText('Только тест');

  consent=false;
  outbox=outbox.map(x=>({...x,status:'SUPPRESSED'}));
  await panel.getByRole('button',{name:'Обновить после отзыва'}).click();
  await expect(panel).toContainText('Подавлено после отзыва');
  await expect(panel).toContainText('Реальная отправка: запрещена.');
});



for(const kind of ['consent','preferences'] as const){
  test('customer '+kind+' confirms receipt, exact retry, conflict recovery and revocation',async({page})=>{
    const isConsent=kind==='consent';
    const endpoint=isConsent?consentUrl:'**/api/v1/dev/account/crm-contact-preferences';
    const scopeName=isConsent?'Тестовое согласие на сервисные уведомления CRM':'Предпочтения способов связи CRM (dev)';
    const channel=isConsent?'Звонок':'Звонки';
    const saveName=isConsent?'Сохранить тестовый выбор: Звонок':'Записать выбор: Звонки';
    const retryName=isConsent?'Повторить с тем же ключом':'Повторить выбор с тем же ключом';
    const refreshName=isConsent?'Обновить согласие':'Обновить предпочтения связи';
    let version=0,enabled=false,mode='badReceipt';
    const posts:any[]=[];
    const snapshot=()=>({syntheticOnly:true,sendAllowed:false,
      ...(isConsent?{purposeCode:'SERVICE_UPDATE',legalConsentVerified:false}:{}),
      channels:{CALL:enabled,EMAIL:false,SMS:false},revisions:{CALL:version,EMAIL:0,SMS:0}});
    await page.route(endpoint,route=>{
      if(route.request().method()==='GET')return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify(snapshot())});
      const payload=route.request().postDataJSON();
      posts.push(payload);
      expect(route.request().headers()['x-csrf-token']).toBe('crm-csrf');
      if(mode==='badReceipt')return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({...snapshot(),replayed:false})});
      if(mode==='replay'){
        expect(payload).toEqual(posts[0]);
        enabled=false;version=2;
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...snapshot(),replayed:true})});
      }
      if(mode==='conflict'){
        expect(payload.expectedRevision).toBe(2);
        enabled=true;version=3;
        return route.fulfill({status:409,contentType:'application/json',body:'{"code":"STALE_REVISION"}'});
      }
      return route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
    });
    await page.setViewportSize({width:390,height:844});
    await page.goto('/account/settings');
    const panel=page.getByLabel(scopeName);
    const checkbox=panel.getByLabel(channel,{exact:true});
    const save=panel.getByRole('button',{name:saveName,exact:true});
    await expect(checkbox).not.toBeChecked();
    await checkbox.check();
    await save.click();
    await expect(panel).toContainText('Ответ неизвестен.');
    await expect(save).toBeDisabled();
    mode='replay';
    await panel.getByRole('button',{name:retryName,exact:true}).click();
    await expect(panel).toContainText('Показано текущее состояние.');
    await expect(checkbox).not.toBeChecked();
    expect(posts).toHaveLength(2);
    expect(posts[1]).toEqual(posts[0]);
    mode='conflict';
    await checkbox.check();
    await save.click();
    await expect(panel.getByRole('alert')).toContainText('Перечитайте текущее состояние');
    await expect(checkbox).toHaveCount(0);
    await expect(save).toHaveCount(0);
    await panel.getByRole('button',{name:refreshName,exact:true}).click();
    await expect(checkbox).toBeChecked();
    mode='denied';
    await checkbox.uncheck();
    await save.click();
    await expect(checkbox).toHaveCount(0);
    await expect(panel).toContainText('Нет доступа');
    expect(posts).toHaveLength(4);
    expect(posts[3].expectedRevision).toBe(3);
    expect(posts[3].enabled).toBe(false);
    expect(posts[3].requestKey).not.toBe(posts[2].requestKey);
  });
}
