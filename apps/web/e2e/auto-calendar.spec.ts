import { test, expect } from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const bay='33333333-3333-4333-8333-333333333333';
const link='44444444-4444-4444-8444-444444444444';
const appointment='55555555-5555-4555-8555-555555555555';
const work='66666666-6666-4666-8666-666666666666';
const base='**/api/v1/dev/auto/organizations/**';

test('calendar denies access without showing previous tenant records',async({page})=>{
  await page.route(base,route=>route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}));
  await page.goto('/');
  await page.getByRole('navigation',{name:'Разделы сервиса'}).getByRole('link',{name:'Календарь (dev)'}).click();
  await expect(page.getByRole('heading',{name:'Календарь приёмщика'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('button',{name:'Создать запись'})).toHaveCount(0);
});

test('calendar creates, reschedules and cancels via exact tenant paths',async({page})=>{
  let current:string|null=null;
  const calls:{path:string;body:unknown}[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route(base,route=>{
    const req=route.request();
    if(req.method()==='GET')return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items:current?[{appointmentId:appointment,bookingId:bay,bayId:bay,
      startsAt:'2030-01-01T10:00:00+00:00',endsAt:'2030-01-01T11:00:00+00:00',workOrderId:work}]:[]})});
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    calls.push({path:new URL(req.url()).pathname,body:req.postDataJSON()});
    if(req.url().endsWith('/cancel')){current=null;return route.fulfill({status:200,contentType:'application/json',body:'{"status":"CANCELED"}'});}
    current='active';
    return route.fulfill({status:200,contentType:'application/json',body:'{"status":"RESCHEDULED"}'});
  });
  await page.goto('/staff/auto-calendar');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByText('Записей нет.')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID поста'}).fill(bay);
  await page.getByRole('textbox',{name:'UUID связи клиента'}).fill(link);
  await page.getByRole('textbox',{name:'Начало ISO'}).fill('2030-01-01T10:00:00Z');
  await page.getByRole('textbox',{name:'Конец ISO'}).fill('2030-01-01T11:00:00Z');
  await page.getByRole('button',{name:'Создать запись'}).click();
  await expect(page.getByText('Appointment '+appointment+' · WorkOrder '+work)).toBeVisible();
  await page.getByRole('button',{name:'Перенести'}).click();
  await page.getByRole('textbox',{name:'Начало ISO'}).fill('2030-01-01T12:00:00Z');
  await page.getByRole('textbox',{name:'Конец ISO'}).fill('2030-01-01T13:00:00Z');
  await page.getByRole('button',{name:'Сохранить перенос'}).click();
  await expect(page.getByText('Appointment '+appointment+' · WorkOrder '+work)).toBeVisible();
  await page.getByRole('button',{name:'Отменить запись'}).click();
  expect(calls).toHaveLength(2);
  await page.getByRole('button',{name:'Подтвердить отмену'}).click();
  await expect(page.getByText('Записей нет.')).toBeVisible();
  expect(calls.map(x=>x.path)).toEqual([
    '/api/v1/dev/auto/organizations/'+org+'/appointments',
    '/api/v1/dev/auto/organizations/'+org+'/appointments/'+appointment+'/reschedule',
    '/api/v1/dev/auto/organizations/'+org+'/appointments/'+appointment+'/cancel']);
});

test('calendar blocks zero and reverse intervals before posting',async({page})=>{
  let posts=0;
  await page.route(base,route=>{
    if(route.request().method()==='POST'){posts++;return route.fulfill({status:201,body:'{}'});}
    return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
  });
  await page.goto('/staff/auto-calendar');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByText('Записей нет.')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID поста'}).fill(bay);
  await page.getByRole('textbox',{name:'UUID связи клиента'}).fill(link);
  await page.getByRole('textbox',{name:'Начало ISO'}).fill('2030-01-01T10:00:00Z');
  await page.getByRole('textbox',{name:'Конец ISO'}).fill('2030-01-01T10:00:00Z');
  await expect(page.getByRole('button',{name:'Создать запись'})).toBeDisabled();
  await page.getByRole('textbox',{name:'Конец ISO'}).fill('2030-01-01T09:00:00Z');
  await expect(page.getByRole('button',{name:'Создать запись'})).toBeDisabled();
  await page.getByRole('textbox',{name:'Конец ISO'}).fill('2030-01-01T11:00:00Z');
  await expect(page.getByRole('button',{name:'Создать запись'})).toBeEnabled();
  expect(posts).toBe(0);
});

test('calendar mobile layout remains in viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.route(base,route=>route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'}));
  await page.goto('/staff/auto-calendar');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByText('Записей нет.')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
