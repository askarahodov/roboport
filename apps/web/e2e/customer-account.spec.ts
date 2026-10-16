import { test, expect } from '@playwright/test';

const routes = [
  '/account', '/account/robots', '/account/tickets',
  '/account/estimates', '/account/documents', '/account/settings',
];

test('all customer routes fail closed without a server session', async ({ page }) => {
  for (const path of routes) {
    await page.goto(path);
    await expect(page.getByRole('alert')).toContainText('403 — доступ запрещён');
    await expect(page.getByText('Сессия подтверждена')).toHaveCount(0);
  }
});

test('customer account does not accept mock identity from localStorage', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.setItem('subjectId', 'fake-client-b'));
  await page.goto('/account/tickets');
  await expect(page.getByRole('alert')).toContainText('403');
  await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
});

test('server 403 and network errors do not expose private sections', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 403, body: '{}' }));
  await page.goto('/account/documents');
  await expect(page.getByRole('alert')).toContainText('доступ запрещён');
  await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
  await page.unroute('**/api/v1/auth/me');
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 503, body: '{}' }));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить');
});

test('mobile + light/dark/auto navigation and keyboard focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/account');
  for (const theme of ['light', 'dark', 'auto']) {
    await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption(theme);
    await expect(page.getByRole('alert')).toContainText('403');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  const link = page.getByRole('navigation', { name: 'Разделы сервиса' }).getByRole('link', { name: 'Личный кабинет' });
  await link.focus();
  await expect(link).toBeFocused();
});


test('local login sends only same-origin credentials and never stores password', async ({ page }) => {
  await page.goto('/account/login');
  await page.getByRole('textbox', { name: 'Логин' }).fill('synthetic-a');
  await page.getByLabel('Пароль').fill('synthetic-secret');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page.getByRole('alert')).toContainText('защищённом локальном HTTPS');
  const stored=await page.evaluate(() => Object.values(localStorage).join('|'));
  expect(stored).not.toContain('synthetic-secret');
  expect(stored).not.toContain('synthetic-a');
  await expect(page.getByRole('heading', { name: 'Вход в личный кабинет' })).toBeVisible();
});


test('six private views render after auth API success, responsive and keyboard accessible', async ({ page }) => {
  // UI contract only; live PostgreSQL A/B authorization is covered separately in tests/integration.
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({subjectId:'00000000-0000-4000-8000-000000000001'}),
  }));
  await page.route('**/api/v1/dev/account/tickets**', route => route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[],limit:20,offset:0}),
  }));
  await page.route('**/api/v1/dev/account/summary/*', route => route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({items:[]}),
  }));
  await page.route('**/api/v1/dev/account/preferences', route => route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({inAppEnabled:true}),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account');
  const nav=page.getByRole('navigation',{name:'Разделы личного кабинета'});
  await expect(nav).toBeVisible();
  for (const [path,title] of [
    ['/account','Обзор'],['/account/robots','Роботы'],
    ['/account/tickets','Заявки'],['/account/estimates','Сметы'],
    ['/account/documents','Документы'],['/account/settings','Настройки и уведомления'],
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading',{name:title})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  for (const theme of ['light','dark','auto']) {
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme);
    await expect(nav).toBeVisible();
  }
  const button=page.getByRole('button',{name:'Выйти'});
  await button.focus();
  await expect(button).toBeFocused();
  await page.screenshot({path:'test-results/account-authenticated-mobile.png',fullPage:true});
});
