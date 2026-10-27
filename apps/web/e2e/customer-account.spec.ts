import { test, expect } from '@playwright/test';

const routes = [
  '/account', '/account/robots', '/account/vehicles', '/account/tickets',
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


test('seven private views render after auth API success, responsive and keyboard accessible', async ({ page }) => {
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
    ['/account','Обзор'],['/account/robots','Роботы'],['/account/vehicles','Автомобили (dev)'],
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


test('overview: private resources are fail-closed and retry replaces errors with real data',async({page})=>{
 await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',
  body:'{"subjectId":"00000000-0000-4000-8000-000000000001"}'}));
 let retry=false;
 await page.route('**/api/v1/dev/account/summary/*',r=>{
  const section=r.request().url().split('/').pop();
  return r.fulfill({status:section==='documents'&&!retry?403:200,contentType:'application/json',
   body:section==='documents'&&!retry?'{}':JSON.stringify({items:section==='robots'?[{id:'synthetic-robot'}]:[]})});
 });
 await page.route('**/api/v1/dev/account/tickets?*',r=>r.fulfill({status:200,contentType:'application/json',
  body:JSON.stringify({items:[{id:'synthetic-ticket'}],limit:20,offset:0})}));
 await page.goto('/account');
 const overview=page.getByRole('region',{name:'Обзор клиентского кабинета'});
 await expect(overview.getByText('записей на странице: 1')).toHaveCount(2);
 await expect(overview.getByText('доступ отозван')).toBeVisible();
 retry=true;
 await overview.getByRole('button',{name:'Обновить обзор'}).click();
 await expect(overview.getByText('доступ отозван')).toHaveCount(0);
 await expect(overview.getByText('нет доступных записей')).toHaveCount(2);
 await expect(page.getByText('Реальные обращения пока не принимаются.')).toHaveCount(0);
});


test('estimate exact SENT version rejects stale 409, retry and revoked rights',async({page})=>{
 await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',body:'{"subjectId":"00000000-0000-4000-8000-000000000001"}'}));
 let revoked=false;
 await page.route('**/api/v1/dev/account/summary/estimates',r=>r.fulfill({status:revoked?403:200,contentType:'application/json',body:revoked?'{}':JSON.stringify({items:[{id:'synthetic-quote',version:2,status:'SENT',total_minor:15000,currency:'RUB'}]})}));
 await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
 let submitted=0;
 await page.route('**/api/v1/customer/estimates/synthetic-quote/approve',r=>{
  submitted++;
  const payload=r.request().postDataJSON();
  expect(payload.estimateVersion).toBe(2);
  expect(r.request().headers()['idempotency-key']).toBeTruthy();
  return r.fulfill({status:409,contentType:'application/json',body:'{"code":"VERSION_CONFLICT"}'});
 });
 await page.goto('/account/estimates');
 await expect(page.getByText(/версия 2/)).toBeVisible();
 await page.getByRole('button',{name:'Согласовать смету'}).click();
 await expect(page.getByText('409 — смета изменилась. Загрузите актуальную версию перед решением.')).toBeVisible();
 expect(submitted).toBe(1);
 revoked=true;
 await page.getByRole('button',{name:'Проверить доступ снова'}).click();
 await page.goto('/account/estimates');
 await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
 await expect(page.getByRole('button',{name:'Согласовать смету'})).toHaveCount(0);
});

test('documents fail closed when revoked, no object key or direct storage links',async({page})=>{
 await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',body:'{"subjectId":"00000000-0000-4000-8000-000000000001"}'}));
 let revoked=false;
 await page.route('**/api/v1/dev/account/summary/documents',r=>r.fulfill({status:revoked?403:200,contentType:'application/json',body:revoked?'{}':JSON.stringify({items:[{id:'synthetic-document',mime:'application/pdf',byte_size:42}]})}));
 await page.goto('/account/documents');
 const link=page.getByRole('link',{name:'Открыть через защищённый сервер'});
 await expect(link).toHaveAttribute('href','/api/v1/dev/attachments/synthetic-document/content');
 await expect(page.locator('a[href*="minio"],a[href*="s3://"]')).toHaveCount(0);
 revoked=true;
 await page.getByRole('button',{name:'Проверить доступ снова'}).click();
 await page.goto('/account/documents');
 await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
 await expect(link).toHaveCount(0);
});

test('account forbidden state provides safe path to login without private resources',async({page})=>{
 await page.route('**/api/v1/auth/me',r=>r.fulfill({status:403,contentType:'application/json',body:'{}'}));
 await page.goto('/account');
 await expect(page.getByRole('alert')).toContainText('403');
 const recovery=page.getByRole('link',{name:'Войти под другой учётной запись'});
 await expect(recovery).toBeVisible();
 await expect(page.getByRole('navigation',{name:'Разделы личного кабинета'})).toHaveCount(0);
 await recovery.click();
 await expect(page).toHaveURL(/\/account\/login$/);
 await expect(page.getByRole('heading',{name:'Вход в личный кабинет'})).toBeVisible();
});

test('login is discoverable directly in global navigation on desktop and mobile',async({page})=>{
 for(const width of [390,1440]){
  await page.setViewportSize({width,height:844});
  await page.goto('/');
  const link=page.getByRole('navigation',{name:'Разделы сервиса'}).getByRole('link',{name:'Войти'});
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/account\/login$/);
 }
});

test('Keycloak entry is visible and points to same-origin guarded OIDC start',async({page})=>{
 await page.goto('/account/login');
 const entry=page.getByRole('link',{name:'Войти через Keycloak'});
 await expect(entry).toBeVisible();
 await expect(entry).toHaveAttribute('href','/api/v1/dev/oidc/start');
 await expect(page.getByRole('heading',{name:'Локальная тестовая учётная запись'})).toBeVisible();
 // A denied development IdP must not grant a session or navigate to private data.
 await page.route('**/api/v1/dev/oidc/start',r=>r.fulfill({status:403,body:'{}'}));
 await entry.click();
 await expect(page).toHaveURL(/\/api\/v1\/dev\/oidc\/start$/);
});


test('ticket timeline is owner-scoped and fails closed after revoked ownership',async({page})=>{
 await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',body:'{"subjectId":"synthetic-a"}'}));
 await page.route('**/api/v1/dev/account/tickets?*',r=>r.fulfill({status:200,contentType:'application/json',
  body:JSON.stringify({items:[{id:'synthetic-ticket-a',state:'DIAGNOSING',version:2}]})}));
 let revoked=false;
 await page.route('**/api/v1/dev/account/tickets/synthetic-ticket-a/timeline',r=>r.fulfill({
   status:revoked?404:200,contentType:'application/json',body:revoked?'{}':
   JSON.stringify({items:[{fromState:'DRAFT',toState:'SUBMITTED',at:'2026-10-26T08:00:00Z',version:2}]})}));
 await page.goto('/account/tickets');
 await page.getByRole('button',{name:'История этапов'}).click();
 await expect(page.getByLabel('История этапов заявки').getByText('SUBMITTED')).toBeVisible();
 await page.getByRole('button',{name:'Скрыть историю'}).click();
 revoked=true;
 await page.getByRole('button',{name:'История этапов'}).click();
 await expect(page.getByText('История недоступна: права изменились.')).toBeVisible();
 await expect(page.getByText('SUBMITTED')).toHaveCount(0);
});
test('new customer has safe empty accounts, no guessed resources',async({page})=>{
 await page.route('**/api/v1/auth/me',r=>r.fulfill({status:200,contentType:'application/json',body:'{"subjectId":"synthetic-new"}'}));
 await page.route('**/api/v1/dev/account/tickets?*',r=>r.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'}));
 await page.goto('/account/tickets');
 await expect(page.getByText('У вас пока нет доступных заявок.')).toBeVisible();
 await expect(page.getByRole('button',{name:'История этапов'})).toHaveCount(0);
});


test('synthetic guest request remains pending and exposes no private ticket data',async({page})=>{
 let attempts=0;
 await page.route('**/api/v1/dev/guest-intake',async route=>{
  attempts++;
  expect(route.request().headers()['idempotency-key']).toBeTruthy();
  expect(route.request().postDataJSON()).toEqual({});
  await route.fulfill({status:202,contentType:'application/json',
   body:'{"requestId":"synthetic-receipt","status":"PENDING_REVIEW"}'});
 });
 await page.goto('/request-demo');
 await expect(page.getByText('Приём реальных обращений закрыт.')).toBeVisible();
 await page.getByRole('button',{name:'Создать тестовую заявку'}).click();
 await expect(page.getByText('pending_review')).toBeVisible();
 await expect(page.getByText('synthetic-receipt')).toBeVisible();
 await expect(page.getByText('Доступ к истории, сметам и документам не предоставлен.')).toBeVisible();
 expect(attempts).toBe(1);
 await page.getByRole('link',{name:'У меня есть доказательство для подтверждения'}).click();
 await expect(page).toHaveURL(/\/account\/claim$/);
 await expect(page.getByRole('heading',{name:'Подтверждение тестовой заявки'})).toBeVisible();
});
test('proof redemption waits for independent approval and never grants automatic access',async({page})=>{
 await page.route('**/api/v1/auth/csrf',route=>route.fulfill({status:200,contentType:'application/json',
  body:'{"csrfToken":"synthetic-csrf"}'}));
 await page.route('**/api/v1/dev/guest-claims/*/redeem',route=>{
  expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
  return route.fulfill({status:200,contentType:'application/json',
   body:'{"status":"PENDING_REVIEW"}'});
 });
 await page.goto('/account/claim');
 await page.getByLabel('Идентификатор подтверждения').fill('synthetic-claim');
 await page.getByLabel('Код подтверждения').fill('synthetic-proofffffffffffffffffffffff');
 await page.getByRole('button',{name:'Подтвердить'}).click();
 await expect(page.getByText(/Доказательство принято. Статус pending_review/)).toBeVisible();
 await expect(page.getByText(/доступ появится только после независимого решения сотрудника/)).toBeVisible();
});
