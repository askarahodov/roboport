import { test, expect } from '@playwright/test';

test('кабинет не показывает профиль при ошибке серверной сессии', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 401, contentType: 'application/json',
    body: JSON.stringify({ code: 'UNAUTHENTICATED' }),
  }));
  await page.goto('/account/robots');
  await expect(page.getByRole('status')).toContainText('401');
  await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
  await expect(page.getByText('Устройства, привязанные')).toHaveCount(0);
});

test('проверенный subject показывает навигацию из шести разделов без раскрытия данных', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ subjectId: '11111111-1111-4111-8111-111111111111' }),
  }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/account');
  await expect(page).toHaveURL(/\/account\/overview$/);
  const nav = page.getByRole('navigation', { name: 'Разделы личного кабинета' });
  await expect(nav.getByRole('link')).toHaveCount(6);
  await nav.getByRole('link', { name: 'Документы' }).click();
  await expect(page.getByRole('heading', { name: 'Документы' })).toBeVisible();
  await expect(page.getByText('Прямого доступа к MinIO нет.')).toBeVisible();
  const keys = await page.evaluate(() => Object.keys(localStorage));
  expect(keys.every(key => key === 'roboport-theme')).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('ошибка backend не подменяется анонимным кабинетом и позволяет retry', async ({ page }) => {
  let healthy = false;
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: healthy ? 200 : 503,
    contentType: 'application/json',
    body: healthy ? '{"subjectId":"11111111-1111-4111-8111-111111111111"}' : '{"code":"SERVICE_UNAVAILABLE"}',
  }));
  await page.goto('/account');
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить серверную сессию');
  healthy = true;
  await page.getByRole('button', { name: 'Повторить проверку' }).click();
  await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toBeVisible();
});

test('light dark auto и клавиатурная навигация приватного shell', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"subjectId":"11111111-1111-4111-8111-111111111111"}',
  }));
  await page.goto('/account');
  for (const theme of ['dark', 'light', 'auto']) {
    await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption(theme);
    await expect(page.getByRole('combobox', { name: 'Тема оформления' })).toHaveValue(theme);
  }
  const robots = page.getByRole('navigation', { name: 'Разделы личного кабинета' })
    .getByRole('link', { name: 'Роботы' });
  await robots.focus();
  await expect(robots).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/account\/robots$/);
  await expect(page.getByRole('alert')).toContainText('Раздел недоступен. Приватные данные не показаны.');
});

test('раздел заявок загружает только серверный ответ; 401 и 404 не показывают чужие данные', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"subjectId":"11111111-1111-4111-8111-111111111111"}',
  }));
  await page.route('**/api/v1/dev/account/collections/tickets', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [
      { id: '22222222-2222-4222-8222-222222222222', state: 'APPROVED', version: 4 },
    ], nextCursor: null }),
  }));
  await page.goto('/account/tickets');
  await expect(page.getByText(/Заявка 22222222-2222-4222-8222-222222222222/)).toBeVisible();
  await page.unroute('**/api/v1/dev/account/collections/tickets');
  await page.route('**/api/v1/dev/account/collections/tickets', route => route.fulfill({
    status: 401, contentType: 'application/json', body: '{"code":"UNAUTHENTICATED"}',
  }));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('доступ отозван');
  await expect(page.getByText(/Заявка 22222222-2222-4222-8222-222222222222/)).toHaveCount(0);
});


test('локальный вход, согласование точной версии и защищённый выход без browser tokens', async ({page}) => {
  let loggedIn=false;
  let quoteState='SENT';
  let consentBody:unknown=null;
  let consentKey='';
  await page.route('**/api/v1/auth/me',route=>route.fulfill({
    status:loggedIn?200:401,contentType:'application/json',
    body:loggedIn?'{"subjectId":"11111111-1111-4111-8111-111111111111"}':'{"code":"UNAUTHENTICATED"}',
  }));
  await page.route('**/api/v1/auth/login',async route=>{
    const credentials=route.request().postDataJSON() as {login:string;password:string};
    expect(credentials.login).toBe('synthetic-client');
    expect(credentials.password).toBe('test-passphrase');
    loggedIn=true;
    await route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({csrfToken:'synthetic-csrf-token-with-enough-characters'})});
  });
  await page.route('**/api/v1/dev/account/collections/estimates',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:[{id:'22222222-2222-4222-8222-222222222222',
      ticketId:'33333333-3333-4333-8333-333333333333',
      state:quoteState,version:3,totalMinor:1099}]}),
  }));
  await page.route('**/api/v1/customer/estimates/*/approve',async route=>{
    consentBody=route.request().postDataJSON();
    consentKey=route.request().headers()['idempotency-key']??'';
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf-token-with-enough-characters');
    quoteState='APPROVED';
    await route.fulfill({status:200,contentType:'application/json',body:'{"status":"APPROVED"}'});
  });
  await page.route('**/api/v1/auth/logout',async route=>{
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf-token-with-enough-characters');
    loggedIn=false;
    await route.fulfill({status:200,contentType:'application/json',body:'{"loggedOut":true}'});
  });
  await page.goto('/account/login');
  await page.getByRole('textbox',{name:'Логин'}).fill('synthetic-client');
  await page.getByLabel('Пароль').fill('test-passphrase');
  await page.getByRole('button',{name:'Войти'}).click();
  await expect(page.getByRole('navigation',{name:'Разделы личного кабинета'})).toBeVisible();
  await page.getByRole('link',{name:'Сметы'}).click();
  await expect(page.getByRole('button',{name:'Согласовать'})).toBeVisible();
  await page.getByRole('button',{name:'Согласовать'}).click();
  await expect(page.getByText('Решение зарегистрировано сервером.')).toBeVisible();
  expect(consentBody).toEqual({estimateVersion:3,approvalMethod:'verified_account'});
  expect(consentKey.length).toBeGreaterThan(15);
  await expect(page.getByRole('button',{name:'Согласовать'})).toHaveCount(0);
  const keys=await page.evaluate(()=>Object.keys(localStorage));
  expect(keys.every(key=>key==='roboport-theme')).toBe(true);
  await page.getByRole('button',{name:'Выйти'}).click();
  await expect(page.getByRole('status')).toContainText('401');
});

test('в случае stale версии UI не подтверждает смету и требует обновления',async ({page})=>{
  await page.route('**/api/v1/auth/me',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:'{"subjectId":"11111111-1111-4111-8111-111111111111"}',
  }));
  await page.route('**/api/v1/dev/account/collections/estimates',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:'{"items":[{"id":"22222222-2222-4222-8222-222222222222","state":"SENT","version":2}]}',
  }));
  await page.goto('/account/estimates');
  await expect(page.getByRole('heading',{name:'Сметы'})).toBeVisible();
  await expect(page.getByText('Для решения по смете требуется повторный локальный вход.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Согласовать'})).toHaveCount(0);
});
