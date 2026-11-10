import { test, expect } from '@playwright/test';

async function grant(page: import('@playwright/test').Page, roles: string[]) {
  await page.route('**/api/v1/dev/staff/access', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ roles }),
  }));
}

test('рабочее место СТО закрыто без сотруднической сессии', async ({ page }) => {
  await page.route('**/api/v1/dev/staff/access', route => route.fulfill({
    status: 401, contentType: 'application/json', body: '{"code":"UNAUTHENTICATED"}',
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('401');
  await expect(page.getByRole('navigation', { name: 'Навигация СТО' })).toHaveCount(0);
});

test('отзыв прав и некорректные роли приводят к отказу без разделов', async ({ page }) => {
  await page.route('**/api/v1/dev/staff/access', route => route.fulfill({
    status: 403, contentType: 'application/json', body: '{}',
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('403');
  await expect(page.getByRole('navigation', { name: 'Навигация СТО' })).toHaveCount(0);
  await page.unrouteAll();
  await grant(page, ['ADMIN', 'CLIENT']);
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить права');
  await expect(page.getByRole('navigation', { name: 'Навигация СТО' })).toHaveCount(0);
});

test('складской сотрудник видит только автомобильные складские разделы', async ({ page }) => {
  await grant(page, ['STOCK']);
  await page.goto('/staff');
  const nav = page.getByRole('navigation', { name: 'Навигация СТО' });
  await expect(nav.getByRole('link', { name: 'Инвентаризация (dev)' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Приход запчастей (dev)' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Сметы СТО (dev)' })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'Обращения CRM (dev)' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Назначенные заявки' })).toHaveCount(0);
  await expect(page.getByText('Обслуживание роботов')).toHaveCount(0);
});

test('инженер видит автомобильную диагностику и QC, но не кассу или CRM', async ({ page }) => {
  await grant(page, ['ENGINEER']);
  await page.goto('/staff');
  const nav = page.getByRole('navigation', { name: 'Навигация СТО' });
  await expect(nav.getByRole('link', { name: 'Диагностика автомобиля (dev)' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Контроль качества (dev)' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Финансовый отчёт (dev)' })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'Обращения CRM (dev)' })).toHaveCount(0);
});

test('перемещение по автомобильным разделам через настоящие ссылки и клавиатуру', async ({ page }) => {
  await grant(page, ['STOCK']);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/staff');
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('dark');
  const link = page.getByRole('navigation', { name: 'Навигация СТО' })
    .getByRole('link', { name: 'Инвентаризация (dev)' });
  await link.focus();
  await expect(link).toBeFocused();
  await expect(link).toHaveAttribute('href', '/staff/warehouse-stocktake');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('старая робототехническая очередь даже не запрашивается после входа', async ({ page }) => {
  await grant(page, ['ENGINEER']);
  let legacyCalls = 0;
  await page.route('**/api/v1/dev/staff/queue?*', route => {
    legacyCalls++;
    return route.fulfill({ status: 200, body: '{"items":[]}' });
  });
  await page.goto('/staff');
  await expect(page.getByRole('heading', { name: 'Рабочее место СТО' })).toBeVisible();
  expect(legacyCalls).toBe(0);
});

test('вся помощь сотрудника доступна при 401/403 и не открывает закрытые операции',async({page})=>{
  for(const status of [401,403,503]){
    await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
      status,contentType:'application/json',body:'{}',
    }));
    await page.setViewportSize({width:390,height:844});
    await page.goto('/staff');
    const help=page.getByRole('navigation',{name:'Справка сотрудника'});
    await expect(help).toBeVisible();
    await expect(help.getByRole('link',{name:'Быстрый старт'})).toHaveAttribute('href','/docs/#/guides/QUICK_START');
    await expect(help.getByRole('link',{name:'Навигатор операций'})).toHaveAttribute('href','/docs/#/guides/ROLE_NAVIGATOR');
    await expect(page.getByRole('navigation',{name:'Навигация СТО'})).toHaveCount(0);
    await expect(page.getByRole('navigation',{name:'Руководства моих ролей'})).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.unrouteAll();
  }
});

test('инструкции ролей строго следуют серверному списку разрешений',async({page})=>{
  const profiles=[
    {roles:['STOCK'],only:'Кладовщик и закупщик',none:'Механик и диагност'},
    {roles:['ENGINEER'],only:'Механик и диагност',none:'Кладовщик и закупщик'},
    {roles:['FINANCE'],only:'Бухгалтер и кассир',none:'Руководитель'},
    {roles:['MANAGER'],only:'Руководитель',none:'Механик и диагност'},
    {roles:['AUDITOR'],only:'Проверка доступов и журналов',none:'Руководитель'},
    {roles:['INTAKE'],only:'Мастер-приёмщик',none:'Бухгалтер и кассир'},
  ];
  for(const profile of profiles){
    await grant(page,profile.roles);
    await page.goto('/staff');
    const nav=page.getByRole('navigation',{name:'Руководства моих ролей'});
    await expect(nav.getByRole('link',{name:profile.only})).toBeVisible();
    await expect(nav.getByRole('link',{name:profile.none})).toHaveCount(0);
    const operationHelp=page.getByLabel('Инструкции доступных разделов');
    const operations=page.getByRole('navigation',{name:'Навигация СТО'}).getByRole('link');
    expect(await operationHelp.getByRole('link').count()).toBe(await operations.count());
    if(profile.roles[0]==='FINANCE'){
      await expect(operationHelp.getByRole('link',{name:/Финансовый отчёт/})).toHaveAttribute('href','/docs/#/guides/finance');
      await expect(operations.filter({hasText:'Приход запчастей'})).toHaveCount(0);
    }
  }
});

test('открытие Docsify из сотруднического кабинета работает в том же dev origin',async({page})=>{
  test.setTimeout(90000);
  await grant(page,['STOCK']);
  await page.goto('/staff');
  const link=page.getByRole('navigation',{name:'Руководства моих ролей'}).getByRole('link',{name:'Кладовщик и закупщик'});
  await link.click();
  await expect(page).toHaveURL(/guides.warehouse/);
  await expect(page.locator('.markdown-section h1')).toContainText('Кладовщик и закупщик',{timeout:25000});
  await expect(page.locator('.markdown-section')).toContainText('current-dev synthetic');
  const missing=await page.request.get('/docs/guides/absent-issue-123.md');
  expect(missing.status()).toBe(404);
});
