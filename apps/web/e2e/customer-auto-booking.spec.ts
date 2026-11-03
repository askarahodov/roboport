import {test,expect} from '@playwright/test';

const owner='00000000-0000-4000-8000-000000000001';
const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const link='33333333-3333-4333-8333-333333333333';
const bay='44444444-4444-4444-8444-444444444444';
const appointment='55555555-5555-4555-8555-555555555555';
const booking='66666666-6666-4666-8666-666666666666';
const service='88888888-8888-4888-8888-888888888888';
const work='77777777-7777-4777-8777-777777777777';
const resources={syntheticOnly:true,items:[{organizationId:org,locationId:loc,
  customerLinkId:link,bayId:bay,timeZone:'Europe/Berlin',
  vehicle:{make:'Synthetic',model:'Sedan'}}]};
const first={startsAt:'2030-01-01T10:00:00Z',endsAt:'2030-01-01T11:00:00Z'};
const second={startsAt:'2030-01-01T12:00:00Z',endsAt:'2030-01-01T13:00:00Z'};

async function auth(page:import('@playwright/test').Page){
  await page.route('**/api/v1/auth/me',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({subjectId:owner}),
  }));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
}

test('client completes own booking, move, cancel and sees no PII on 390/1280',async({page})=>{
  await auth(page);
  let status:'EMPTY'|'ACTIVE'|'CANCELED'='EMPTY';
  let interval=first;
  const posts:{path:string;body:Record<string,string>;key:string|undefined}[]=[];
  await page.route('**/api/v1/dev/auto/customer/booking/**',route=>{
    const req=route.request(), url=new URL(req.url());
    if(req.method()==='GET'&&url.pathname.endsWith('/resources'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(resources)});
    if(req.method()==='GET'&&url.pathname.endsWith('/appointments'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,items:status==='EMPTY'?[]:[{
          appointmentId:appointment,organizationId:org,locationId:loc,
          customerLinkId:link,bookingId:booking,bayId:bay,
          ...interval,status:status==='CANCELED'?'CANCELED':'SCHEDULED',
          workOrderId:work,timeZone:'Europe/Berlin',canManage:status==='ACTIVE',
        }],
      })});
    if(req.method()==='GET'&&url.pathname.endsWith('/services'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,items:[{id:service,code:'SVC-001',title:'Тестовая работа',
          durationMinutes:30}],
      })});
    if(req.method()==='GET'&&url.pathname.endsWith('/slots')){
      expect(url.searchParams.get('localDay')).toBe('2030-01-01');
      expect(url.searchParams.get('customerLinkId')).toBe(link);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,timeZone:'Europe/Berlin',guaranteed:false,items:[first,second],
      })});
    }
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    posts.push({path:url.pathname,body:req.postDataJSON(),key:req.headers()['idempotency-key']});
    if(url.pathname.endsWith('/cancel')){
      status='CANCELED';
      return route.fulfill({status:200,contentType:'application/json',
        body:'{"status":"CANCELED"}'});
    }
    if(url.pathname.endsWith('/reschedule')){
      interval=second;
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({status:'RESCHEDULED',bookingId:booking})});
    }
    status='ACTIVE';interval=first;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({appointmentId:appointment,bookingId:booking,workOrderId:work})});
  });

  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/auto-booking');
  const region=page.getByRole('region',{name:'Самостоятельная запись автомобиля (dev)'});
  await expect(region.getByText('Записей пока нет.')).toBeVisible();
  await expect(region.getByLabel('Услуга SVC-001')).toBeVisible();
  // Полный сценарий переноса/отмены остаётся без выбора услуг.
  await region.getByLabel('День филиала').fill('2030-01-01');
  await region.getByRole('button',{name:'Показать свободные окна'}).click();
  await expect(region.getByText('Кандидаты не гарантируют бронь.')).toBeVisible();
  await region.getByRole('button',{name:/01\.01\.2030.*11:00/}).click();
  await expect(region.getByText('Заказ-наряд '+work,{exact:false})).toBeVisible();
  await expect(region.getByRole('button',{name:'Перенести свою запись'})).toBeVisible();
  await region.getByRole('button',{name:'Перенести свою запись'}).click();
  await region.getByRole('button',{name:'Показать свободные окна'}).click();
  await region.getByRole('button',{name:/01\.01\.2030.*13:00/}).click();
  await expect(region.getByText('Заказ-наряд '+work,{exact:false})).toBeVisible();
  await region.getByRole('button',{name:'Отменить свою запись'}).click();
  await expect(region.getByText('Отменена',{exact:false})).toBeVisible();
  await expect(region.getByRole('button',{name:'Перенести свою запись'})).toHaveCount(0);
  expect(posts.map(p=>p.path)).toEqual([
    '/api/v1/dev/auto/customer/booking/appointments',
    '/api/v1/dev/auto/customer/booking/appointments/'+appointment+'/reschedule',
    '/api/v1/dev/auto/customer/booking/appointments/'+appointment+'/cancel',
  ]);
  expect(posts[0].key).toMatch(/^[0-9a-f-]{36}$/);
  expect(posts[1].key).toMatch(/^[0-9a-f-]{36}$/);
  expect(posts[2].key).toBeUndefined();
  expect(posts[0].body.customerLinkId).toBe(link);
  expect(posts[0].body.serviceIds).toEqual([]);
  expect(posts[1].body.organizationId).toBe(org);
  expect(JSON.stringify(resources)).not.toContain('vin');
  await page.setViewportSize({width:1280,height:800});
  await expect(region.getByRole('heading',{name:'Запись автомобиля на сервис'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('customer booking hides own resources immediately after 403',async({page})=>{
  await auth(page);
  let forbidden=false;
  await page.route('**/api/v1/dev/auto/customer/booking/**',route=>{
    if(forbidden)return route.fulfill({status:403,body:'{"code":"FORBIDDEN"}'});
    const suffix=new URL(route.request().url()).pathname;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(suffix.endsWith('/resources')?resources:
        {items:[],syntheticOnly:true})});
  });
  await page.goto('/account/auto-booking');
  const region=page.getByRole('region',{name:'Самостоятельная запись автомобиля (dev)'});
  await expect(region.getByRole('combobox',{name:'Автомобиль и пост'})).toHaveValue(link+':'+bay);
  forbidden=true;
  await region.getByRole('button',{name:'Обновить мои записи'}).click();
  await expect(region.getByRole('alert')).toContainText('Доступ к записи запрещён');
  await expect(region.getByText('Synthetic Sedan',{exact:false})).toHaveCount(0);
});


test('selected workshop service is sent with the first booking in one payload',async({page})=>{
  await auth(page);
  let saved:Record<string,unknown>|null=null;
  await page.route('**/api/v1/dev/auto/customer/booking/**',route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='GET'&&url.pathname.endsWith('/resources'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(resources)});
    if(req.method()==='GET'&&url.pathname.endsWith('/appointments'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,items:[],
      })});
    if(req.method()==='GET'&&url.pathname.endsWith('/services'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,items:[{id:service,code:'SVC-001',title:'Тестовая работа',
          durationMinutes:30}],
      })});
    if(req.method()==='GET'&&url.pathname.endsWith('/slots'))
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,guaranteed:false,timeZone:'Europe/Berlin',items:[first],
      })});
    if(req.method()==='POST'&&url.pathname.endsWith('/appointments')){
      saved=req.postDataJSON();
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({appointmentId:appointment,bookingId:booking,workOrderId:work})});
    }
    return route.fulfill({status:403,body:'{"code":"FORBIDDEN"}'});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/auto-booking');
  const region=page.getByRole('region',{name:'Самостоятельная запись автомобиля (dev)'});
  await expect(region.getByLabel('Услуга SVC-001')).toBeVisible();
  await region.getByLabel('Услуга SVC-001').check();
  await region.getByLabel('День филиала').fill('2030-01-01');
  await region.getByRole('button',{name:'Показать свободные окна'}).click();
  await region.getByRole('button',{name:/01\.01\.2030.*11:00/}).click();
  await expect.poll(()=>saved).not.toBeNull();
  expect(saved?.serviceIds).toEqual([service]);
  expect(saved?.customerLinkId).toBe(link);
});
