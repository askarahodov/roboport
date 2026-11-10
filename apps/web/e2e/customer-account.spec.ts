import { test, expect } from '@playwright/test';

const subject = '00000000-0000-4000-8000-000000000001';
async function authorize(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({subjectId: subject}),
  }));
}

test('автомобильные разделы закрыты без подтверждённой серверной сессии', async ({ page }) => {
  for (const path of ['/account', '/account/vehicles', '/account/auto-booking', '/account/auto-orders', '/account/settings']) {
    await page.goto(path);
    await expect(page.getByRole('alert')).toContainText('403 — доступ запрещён');
    await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
  }
});

test('localStorage не даёт права клиента и не сохраняет пароль', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.setItem('subjectId','fake-user'));
  await page.goto('/account/vehicles');
  await expect(page.getByRole('alert')).toContainText('403');
  await page.goto('/account/login');
  await page.getByRole('textbox', { name: 'Логин' }).fill('synthetic-a');
  await page.getByLabel('Пароль').fill('synthetic-secret');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page.getByRole('alert')).toContainText('защищённом локальном HTTPS');
  const stored = await page.evaluate(() => Object.values(localStorage).join('|'));
  expect(stored).not.toContain('synthetic-secret');
});

test('ошибки 403/503 не открывают автомобильные карточки', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 403, body: '{}' }));
  await page.goto('/account/vehicles');
  await expect(page.getByRole('alert')).toContainText('доступ запрещён');
  await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
  await page.unrouteAll();
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 503, body: '{}' }));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить');
});

test('после авторизации навигация содержит только автомобильные разделы', async ({ page }) => {
  await authorize(page);
  await page.route('**/api/v1/dev/auto/vehicles?*', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"items":[]}',
  }));
  await page.route('**/api/v1/dev/auto/customer/orders?*', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"items":[],"nextCursor":null,"syntheticOnly":true,"releaseAllowed":false}',
  }));
  await page.route('**/api/v1/dev/account/preferences', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"inAppEnabled":true}',
  }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/account');
  const nav = page.getByRole('navigation', { name: 'Разделы личного кабинета' });
  await expect(nav).toBeVisible();
  for (const [path, heading] of [
    ['/account','Обзор'],
    ['/account/vehicles','Автомобили (dev)'],
    ['/account/auto-booking','Запись на сервис (dev)'],
    ['/account/auto-orders','Мои заказ-наряды (dev)'],
    ['/account/settings','Настройки и уведомления'],
  ]) {
    await expect(nav.getByRole('link',{name:heading})).toHaveAttribute('href',path);
  }
  for (const old of ['/account/robots','/account/tickets','/account/estimates','/account/documents']) {
    await expect(nav.locator('a[href="'+old+'"]')).toHaveCount(0);
    await page.goto(old);
    await expect(page.getByRole('heading',{name:'Раздел не найден'})).toBeVisible();
  }
  await page.goto('/account');
  await expect(page.getByRole('region',{name:'Обзор автомобильного кабинета'})).toBeVisible();
  for (const theme of ['light','dark','auto']) {
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  const logout = page.getByRole('button',{name:'Выйти'});
  await logout.focus();
  await expect(logout).toBeFocused();
});

test('автомобильный обзор не загружает роботов или старые заявки', async ({ page }) => {
  await authorize(page);
  let legacyCalls=0;
  await page.route('**/api/v1/dev/account/**', route => {
    if (/\/summary\/|\/tickets/.test(route.request().url())) legacyCalls++;
    return route.fulfill({status:403,body:'{}'});
  });
  await page.goto('/account');
  await expect(page.getByRole('link', {name:/Мои автомобили/})).toBeVisible();
  await expect(page.getByRole('region',{name:'Обзор автомобильного кабинета'}).getByRole('link', {name:'Мои заказ-наряды',exact:true})).toBeVisible();
  expect(legacyCalls).toBe(0);
});

test('восстановление доступа ведёт к логину, но не раскрывает историю', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 403, body: '{}' }));
  await page.goto('/account');
  await expect(page.getByRole('alert')).toContainText('403');
  await page.getByRole('link',{name:'Войти под другой учётной запись'}).click();
  await expect(page).toHaveURL(/\/account\/login$/);
  await expect(page.getByRole('heading',{name:'Вход в личный кабинет'})).toBeVisible();
});

test('вход в кабинет виден при ширине 390 и 1440',async ({page})=>{
  for(const width of [390,1440]){
    await page.setViewportSize({width,height:844});
    await page.goto('/');
    await expect(page.getByRole('navigation',{name:'Разделы сервиса'}).getByRole('link',{name:'Войти'})).toBeVisible();
  }
});

test('справка БоксРитм доступна из каждой вкладки и при отказе без утечки сессии', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/vehicles');
  await expect(page.getByRole('alert')).toContainText('403');
  const deniedHelp=page.getByRole('navigation',{name:'Помощь клиенту'});
  await expect(deniedHelp.getByRole('link',{name:'Как пользоваться кабинетом'}))
    .toHaveAttribute('href','/docs/#/guides/client');
  await expect(deniedHelp.getByRole('link',{name:'Частые вопросы и ошибки'}))
    .toHaveAttribute('href','/docs/#/guides/FAQ');
  await authorize(page);
  for (const path of ['/account','/account/vehicles','/account/auto-booking','/account/auto-orders','/account/settings']) {
    await page.goto(path);
    const help=page.getByRole('navigation',{name:'Помощь клиенту'});
    await expect(help).toBeVisible();
    await expect(help.getByRole('link',{name:'Как пользоваться кабинетом'}))
      .toHaveAttribute('href','/docs/#/guides/client');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  await page.goto('/account/login');
  const loginHelp=page.getByRole('navigation',{name:'Помощь клиенту'});
  await expect(loginHelp.getByRole('link',{name:'Инструкция по входу и восстановлению доступа'}))
    .toHaveAttribute('href','/docs/#/guides/client');
  await loginHelp.getByRole('link',{name:'Инструкция по входу и восстановлению доступа'}).click();
  await expect(page).toHaveURL(/guides.client/);
  await expect(page.locator('.markdown-section h1')).toContainText('Клиент автосервиса');
  await expect(page.locator('.markdown-section')).toContainText('Быстрый путь по кабинету');
  const stored=await page.evaluate(()=>Object.keys(localStorage));
  expect(stored.join('|')).not.toContain('synthetic-secret');
});
