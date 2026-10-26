import { test, expect } from '@playwright/test';

test('staff portal denies anonymous sessions', async ({page}) => {
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:401,contentType:'application/json',body:'{"code":"UNAUTHENTICATED"}'
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('401');
  await expect(page.getByText('Доступные разделы')).toHaveCount(0);
});

test('staff portal does not render staff sections for customer sessions', async ({page}) => {
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('403');
  await expect(page.getByRole('listitem').filter({hasText:'Приёмка'})).toHaveCount(0);
});

test('staff portal shows only explicitly granted roles', async ({page}) => {
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({roles:['STOCK']})
  }));
  await page.goto('/staff');
  await expect(page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Склад/})).toBeVisible();
  await expect(page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Диагностика/})).toHaveCount(0);
  await expect(page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Приёмка/})).toHaveCount(0);
});

test('staff portal fails closed on malformed permissions', async ({page}) => {
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({roles:['ADMIN','CLIENT']})
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить права');
  await expect(page.getByText('Доступные разделы')).toHaveCount(0);
});


test('assigned staff queue renders only backend scoped tickets',async ({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({roles:['INTAKE']})
  }));
  await page.route('**/api/v1/dev/staff/queue?*',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[{id:'00000000-0000-0000-0000-000000000001',state:'TRIAGE',version:2}]})
  }));
  await page.goto('/staff');
  await expect(page.getByRole('region',{name:'Назначенные заявки'})).toContainText('TRIAGE');
  await expect(page.getByRole('region',{name:'Назначенные заявки'})).toContainText('Версия 2');
});

test('queue request forbidden after role revocation hides ticket data',async ({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({roles:['ENGINEER']})
  }));
  await page.route('**/api/v1/dev/staff/queue?*',route=>route.fulfill({
    status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('Доступ к очереди отозван');
  await expect(page.getByRole('region',{name:'Назначенные заявки'}).getByRole('listitem')).toHaveCount(0);
});


test('intake transition submits exact version with CSRF and refreshed queue',async ({page})=>{
  let state='AWAITING_DELIVERY',version=3;
  let posted=false;
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"roles":["INTAKE"]}'
  }));
  await page.route('**/api/v1/dev/staff/queue?*',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[{id:'00000000-0000-0000-0000-000000000001',state,version}]})
  }));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'
  }));
  await page.route('**/api/v1/dev/tickets/*/transition',async route=>{
    const req=route.request();
    expect(req.method()).toBe('POST');
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    expect(req.headers()['idempotency-key']).toBeTruthy();
    expect(req.postDataJSON()).toEqual({toState:'RECEIVED',expectedVersion:3});
    posted=true;state='RECEIVED';version=4;
    await route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({id:'00000000-0000-0000-0000-000000000001',state,version})});
  });
  await page.goto('/staff');
  await page.getByRole('button',{name:'Принять устройство'}).click();
  await expect(page.getByRole('region',{name:'Назначенные заявки'})).toContainText('RECEIVED');
  expect(posted).toBe(true);
});

test('safety hold does not show repair or handover actions',async ({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"roles":["ENGINEER"]}'
  }));
  await page.route('**/api/v1/dev/staff/queue?*',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[{id:'00000000-0000-0000-0000-000000000001',
      state:'SAFETY_HOLD',version:2}]})
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('Safety hold');
  await expect(page.getByRole('button',{name:'Выдать устройство'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Вернуть в ремонт'})).toHaveCount(0);
});

test('QC failed cannot expose handover and stale transition returns 409',async ({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"roles":["ENGINEER"]}'
  }));
  await page.route('**/api/v1/dev/staff/queue?*',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[{id:'00000000-0000-0000-0000-000000000001',
      state:'QC_FAILED',version:4}]})
  }));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'
  }));
  await page.route('**/api/v1/dev/tickets/*/transition',route=>route.fulfill({
    status:409,contentType:'application/json',body:'{"code":"CONFLICT"}'
  }));
  await page.goto('/staff');
  await expect(page.getByRole('button',{name:'Выдать устройство'})).toHaveCount(0);
  await page.getByRole('button',{name:'Вернуть в ремонт'}).click();
  await expect(page.getByRole('status').filter({hasText:'409'})).toBeVisible();
});


test('stock role can only access stock panel and no customer PII',async ({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"roles":["STOCK"]}'
  }));
  await page.goto('/staff');
  await expect(page.getByRole('region',{name:'Операции склада'})).toBeVisible();
  await expect(page.getByRole('region',{name:'Работа со сметами'})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Назначенные заявки'})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Журнал аудита'})).toHaveCount(0);
});

test('auditor role journal is server scoped and does not offer mutation',async ({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"roles":["AUDITOR"]}'
  }));
  await page.route('**/api/v1/dev/staff/audit?*',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[{id:'evt-1',ticketId:'ticket-1',action:'ticket.transition',
      createdAt:'2026-10-16T10:00:00Z'}]})
  }));
  await page.goto('/staff');
  await expect(page.getByRole('region',{name:'Журнал аудита'})).toContainText('ticket.transition');
  await expect(page.getByRole('button',{name:'Выдать устройство'})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Работа со сметами'})).toHaveCount(0);
});

test('mobile dark staff page is keyboard reachable without horizontal overflow',async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"roles":["STOCK"]}'
  }));
  await page.goto('/staff');
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main').first()).toHaveClass(/dark/);
  await page.getByRole('button',{name:'Зарезервировать'}).focus();
  await expect(page.getByRole('button',{name:'Зарезервировать'})).toBeFocused();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('CRM filters only backend assigned tickets and role navigation stays isolated',async({page})=>{
 await page.route('**/api/v1/dev/staff/access',r=>r.fulfill({status:200,contentType:'application/json',body:'{"roles":["ENGINEER"]}'}));
 await page.route('**/api/v1/dev/staff/queue?*',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[
  {id:'aaaa1111-0000-0000-0000-000000000001',state:'DIAGNOSING',version:2},
  {id:'bbbb2222-0000-0000-0000-000000000002',state:'QC_FAILED',version:4}
 ]})}));
 await page.goto('/staff');
 const queue=page.getByRole('region',{name:'Назначенные заявки'});
 await expect(queue.getByRole('listitem')).toHaveCount(2);
 await page.getByRole('textbox',{name:'Поиск по номеру'}).fill('bbbb2222');
 await expect(queue.getByRole('listitem')).toHaveCount(1);
 await expect(queue).toContainText('QC_FAILED');
 await page.getByRole('textbox',{name:'Поиск по номеру'}).fill('');
 await page.getByRole('combobox',{name:'Статус'}).selectOption('DIAGNOSING');
 await expect(queue.getByRole('listitem')).toHaveCount(1);
 await expect(page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Склад/})).toHaveCount(0);
 await page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Диагностика/}).click();
 await expect(queue).toBeVisible();
});
test('CRM remains within 390px viewport in dark theme with role-specific navigation',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/v1/dev/staff/access',r=>r.fulfill({status:200,contentType:'application/json',body:'{"roles":["STOCK"]}'}));
 await page.goto('/staff');
 await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
 await expect(page.getByRole('region',{name:'Операции склада'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('CRM labels are Russian and guest proof survives switching sections',async({page})=>{
 await page.route('**/api/v1/dev/staff/access',r=>r.fulfill({status:200,contentType:'application/json',body:'{"roles":["INTAKE"]}'}));
 await page.route('**/api/v1/dev/staff/queue?*',r=>r.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'}));
 await page.route('**/api/v1/dev/guest-intake/queue',r=>r.fulfill({status:200,contentType:'application/json',body:'{"items":[{"id":"guest-1","createdAt":"2026-10-26T10:00:00Z"}]}'}));
 await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}'}));
 await page.route('**/api/v1/dev/guest-intake/guest-1/assign',r=>r.fulfill({status:200,contentType:'application/json',body:'{"ticketId":"ticket-1"}'}));
 await page.route('**/api/v1/dev/guest-claims/tickets/ticket-1/issue',r=>r.fulfill({status:200,contentType:'application/json',body:'{"claimId":"claim-1","proof":"SYNTHETIC-PROOF"}'}));
 await page.goto('/staff');
 await expect(page.getByText('КАБИНЕТ СОТРУДНИКА')).toBeVisible();
 await expect(page.getByText('Доступ подтверждён')).toBeVisible();
 await page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Приёмка/}).click();
 await page.getByRole('button',{name:'Назначить и выдать код'}).click();
 await page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Очередь/}).click();
 await page.getByRole('navigation',{name:'Навигация CRM'}).getByRole('button',{name:/Приёмка/}).click();
 await expect(page.getByText('SYNTHETIC-PROOF')).toBeVisible();
 await page.getByRole('button',{name:'Скрыть код'}).click();
 await expect(page.getByText('SYNTHETIC-PROOF')).toHaveCount(0);
});
