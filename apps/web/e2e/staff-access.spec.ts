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
  await expect(page.getByText(/Склад — операции ещё не подключены/)).toBeVisible();
  await expect(page.getByText(/Диагностика — операции ещё не подключены/)).toHaveCount(0);
  await expect(page.getByText(/Приёмка — операции ещё не подключены/)).toHaveCount(0);
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
  await expect(page.getByRole('region',{name:'Назначенные заявки'})).toContainText('версия 2');
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
