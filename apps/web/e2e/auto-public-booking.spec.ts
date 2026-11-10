import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const offer='33333333-3333-4333-8333-333333333333';
const bay='44444444-4444-4444-8444-444444444444';
const service='55555555-5555-4555-8555-555555555555';
const link='66666666-6666-4666-8666-666666666666';
const reqId='77777777-7777-4777-8777-777777777777';
const challengeId='88888888-8888-4888-8888-888888888888';
const appointment='99999999-9999-4999-8999-999999999999';
const sku='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const pub='/api/v1/dev/auto/public-booking';
const staff='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/public-booking';
const slot={startsAt:'2030-01-01T11:00:00Z',endsAt:'2030-01-01T11:30:00Z'};
const offers={syntheticOnly:true,publicProduction:false,guaranteed:false,capturePII:false,
  items:[{id:offer,organizationId:org,locationId:loc,bayId:bay,serviceId:service,
    policyVersion:'DEV-CONSENT-2026',serviceTitle:'Синтетический осмотр',
    durationMinutes:30,bayCode:'BAY-1',timeZone:'Europe/Berlin',materialsReady:true}]};

test('public 390 guest no-PII -> exact lost response retry -> verified client -> staff 1280 fulfill with PART/CRM',async({page},info)=>{
  let registered=false,claimed=false,booked=false,withdrawn=false;
  let secret='';
  let firstLost=true;
  const attempts:Record<string,unknown>[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    const path=url.pathname,verb=request.method();
    if(path===pub+'/offers'&&verb==='GET')
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(offers)});
    if(path===pub+'/slots'&&verb==='GET'){
      expect(url.searchParams.get('offerId')).toBe(offer);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,guaranteed:false,materialsReady:true,
        timeZone:'Europe/Berlin',items:[slot],
      })});
    }
    if(path===pub+'/challenges'&&verb==='POST'){
      expect(request.postDataJSON().offerId).toBe(offer);
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,challengeId,question:'6 + 3 = ?',
        captchaProvider:'DEV_ARITHMETIC_ONLY',expiresInSeconds:300,
      })});
    }
    if(path===pub+'/requests'&&verb==='POST'){
      const data=request.postDataJSON() as Record<string,unknown>;
      expect(data).toMatchObject({offerId:offer,challengeId,answer:9,
        startsAt:slot.startsAt,endsAt:slot.endsAt,processingConsent:true,
        marketingConsent:true});
      attempts.push(data);
      secret=String(data.requestKey);
      if(firstLost){firstLost=false;return route.abort('failed');}
      registered=true;
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({requestId:reqId,receiptKey:secret,
          replayed:true,status:'AWAITING_VERIFICATION',noSlotHold:true,syntheticOnly:true})});
    }
    if(path===pub+'/requests/'+reqId+'/status'&&verb==='POST'){
      expect(request.postDataJSON().receiptKey).toBe(secret);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,realDelivery:false,requestId:reqId,
        noSlotHold:!booked,status:booked?'BOOKED':claimed?'WAITING_FOR_STAFF':'AWAITING_VERIFICATION',
        marketingEnabled:!withdrawn,appointmentId:booked?appointment:null,
        workOrderId:null,
      })});
    }
    if(path===pub+'/requests/'+reqId+'/claim'&&verb==='POST'){
      expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
      expect(request.postDataJSON()).toMatchObject({
        customerLinkId:link,receiptKey:secret,processingConsent:true});
      claimed=true;
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,requestId:reqId,status:'WAITING_FOR_STAFF',
        noSlotHold:true,realDelivery:false,marketingEnabled:true,
      })});
    }
    if(path===pub+'/requests/'+reqId+'/withdraw'&&verb==='POST'){
      expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
      withdrawn=true;
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,requestId:reqId,status:'WAITING_FOR_STAFF',
        marketingEnabled:false,
      })});
    }
    if(path===staff+'/queue'&&verb==='GET'){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,page:0,hasMore:false,items:registered?[{
          requestId:reqId,offerId:offer,startsAt:slot.startsAt,
          endsAt:slot.endsAt,createdAt:'2030-01-01T10:00:00Z',
          status:booked?'BOOKED':claimed?'WAITING_FOR_STAFF':'AWAITING_VERIFICATION',
          appointmentId:booked?appointment:null,
          materials:[{skuId:sku,kind:'PART',quantity:2}],
        }]:[],
      })});
    }
    if(path===staff+'/requests/'+reqId+'/fulfill'&&verb==='POST'){
      expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
      expect(request.postDataJSON().requestKey).toMatch(/^[a-f0-9-]{36}$/);
      booked=true;
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,replayed:false,
          releaseAllowed:false,appointmentId:appointment,workOrderId:reqId,crmLeadId:offer})});
    }
    throw Error('Unexpected route '+verb+' '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/book-dev');
  const publicPage=page.getByRole('region',{name:'Публичная онлайн-запись (только DEV)'});
  await publicPage.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await publicPage.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await publicPage.getByRole('button',{name:'Найти услуги'}).click();
  await publicPage.getByLabel('Услуга и пост').selectOption(offer);
  await publicPage.getByLabel('Дата').fill('2030-01-01');
  await publicPage.getByRole('button',{name:'Показать время'}).click();
  await publicPage.getByLabel('Время').selectOption(slot.startsAt);
  await publicPage.getByRole('checkbox',{name:/ознакомление с тестовыми условиями/}).check();
  await publicPage.getByRole('checkbox',{name:/маркетинговых коммуникаций/}).check();
  await publicPage.getByRole('button',{name:'Получить проверочный вопрос'}).click();
  await publicPage.getByRole('textbox',{name:/Антибот-вопрос DEV/}).fill('9');
  await publicPage.getByRole('button',{name:/Оставить заявку/}).click();
  await expect(publicPage.getByRole('button',{name:'Повторить исходный запрос'})).toBeVisible();
  await publicPage.getByRole('button',{name:'Повторить исходный запрос'}).click();
  await expect(publicPage.getByRole('region',{name:'Секретная квитанция заявки'})).toContainText(reqId);
  expect(attempts).toHaveLength(2);
  expect(attempts[0]).toEqual(attempts[1]);
  await page.screenshot({path:info.outputPath('public-booking-390.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.goto('/account/auto-public-booking');
  const client=page.getByRole('region',{name:'Подтверждение публичной заявки клиентом (DEV)'});
  await client.getByRole('textbox',{name:'ID заявки'}).fill(reqId);
  await client.getByRole('textbox',{name:'Секретный ключ квитанции'}).fill(secret);
  await client.getByRole('textbox',{name:'UUID моей verified-связи'}).fill(link);
  await client.getByRole('button',{name:/Подтвердить владельца/}).click();
  await expect(client.getByText('WAITING_FOR_STAFF.',{exact:false})).toBeVisible();
  await client.getByRole('button',{name:'Отозвать тестовый выбор маркетинга'}).click();
  await expect(client.getByText('маркетинг отключён',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.goto('/staff/auto-public-booking');
  const staffUI=page.getByRole('region',{name:'Публикации и публичные заявки СТО (DEV)'});
  await staffUI.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await staffUI.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await staffUI.getByRole('button',{name:'Обновить очередь'}).click();
  await expect(staffUI.getByText('WAITING_FOR_STAFF',{exact:false})).toBeVisible();
  await expect(staffUI.getByText('PART '+sku+' ×2',{exact:false})).toBeVisible();
  await staffUI.getByRole('button',{name:'Создать запись, CRM-лид и резерв атомарно'}).click();
  await expect(staffUI.getByText('BOOKED',{exact:false})).toBeVisible();
  await expect(staffUI.getByText('Запись: '+appointment,{exact:false})).toBeVisible();
  await page.screenshot({path:info.outputPath('public-queue-1280.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('staff 403 after revoked grant clears formerly visible requests',async({page})=>{
  let revoked=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path===staff+'/queue'){
      return route.fulfill({status:revoked?403:200,contentType:'application/json',
        body:revoked?'{"code":"FORBIDDEN"}':JSON.stringify({syntheticOnly:true,page:0,hasMore:false,items:[{
          requestId:reqId,offerId:offer,startsAt:slot.startsAt,
          endsAt:slot.endsAt,createdAt:'2030-01-01T00:00:00Z',
          status:'WAITING_FOR_STAFF',appointmentId:null,materials:[],
        }]})});
    }
    throw Error('Unexpected '+path);
  });
  await page.goto('/staff/auto-public-booking');
  const staffUI=page.getByRole('region',{name:'Публикации и публичные заявки СТО (DEV)'});
  await staffUI.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await staffUI.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await staffUI.getByRole('button',{name:'Обновить очередь'}).click();
  await expect(staffUI.getByText('WAITING_FOR_STAFF',{exact:false})).toBeVisible();
  revoked=true;
  await staffUI.getByRole('button',{name:'Обновить очередь'}).click();
  await expect(staffUI.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(staffUI.getByText('WAITING_FOR_STAFF',{exact:false})).toHaveCount(0);
});


test('manager withdraws publication: offer vanishes, exact CSRF key and no false booking',async({page})=>{
  let revoked=false;
  const mutations:Record<string,unknown>[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(path===staff+'/offers'&&req.method()==='GET'){
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({...offers,items:revoked?[]:offers.items})});
    }
    if(path===staff+'/offers/'+offer+'/revoke'&&req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      mutations.push(body);revoked=true;
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"syntheticOnly":true,"revoked":true,"replayed":false}'});
    }
    throw Error('Unexpected '+path);
  });
  await page.goto('/staff/auto-public-booking');
  const ui=page.getByRole('region',{name:'Публикации и публичные заявки СТО (DEV)'});
  await ui.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await ui.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await ui.getByRole('button',{name:'Показать публикации'}).click();
  await expect(ui.getByText('Синтетический осмотр',{exact:false})).toBeVisible();
  await ui.getByRole('button',{name:'Отозвать публикацию'}).click();
  expect(mutations).toHaveLength(1);
  await expect(ui.getByRole('button',{name:'Отозвать публикацию'})).toHaveCount(0);
});
