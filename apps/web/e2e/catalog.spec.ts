import { test, expect } from '@playwright/test';

test('переходы по каталогу и пустые состояния через реальный API', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Сервис обслуживания роботов' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Разделы сервиса' }).getByRole('link', { name: 'Бренды' }).click();
  await expect(page).toHaveURL(/\/catalog\/brands$/);
  await expect(page.getByRole('heading', { name: 'Бренды роботов' })).toBeVisible();
  await expect(page.getByText('Пока нет опубликованных брендов.')).toBeVisible();
  await page.getByRole('navigation', { name: 'Разделы сервиса' }).getByRole('link', { name: 'Модели' }).click();
  await expect(page).toHaveURL(/\/catalog\/models$/);
  await expect(page.getByText('Пока нет опубликованных моделей.')).toBeVisible();
});

test('недоступный API показывает ошибку, не фиктивные данные', async ({ page }) => {
  await page.route('**/api/v1/catalog/brands?*', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ code: 'SERVICE_UNAVAILABLE' }),
  }));
  await page.goto('/catalog/brands');
  await expect(page.getByRole('alert')).toContainText('Каталог сейчас недоступен');
  await expect(page.getByText('Пока нет опубликованных брендов.')).toHaveCount(0);
});

test('переключение темы и отсутствие формы реальных заявок', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('dark');
  await expect(page.locator('main')).toHaveClass(/dark/);
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('light');
  await expect(page.locator('main')).not.toHaveClass(/dark/);
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('auto');
  await expect(page.getByText('Приём реальных заявок пока закрыт.')).toBeVisible();
  await expect(page.locator('form')).toHaveCount(0);
});

test('неизвестный URL не маскируется главной страницей', async ({ page }) => {
  await page.goto('/unknown-section');
  await expect(page.getByRole('heading', { name: 'Страница не найдена' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Сервис обслуживания роботов' })).toHaveCount(0);
  await page.getByRole('link', { name: 'Вернуться на главную' }).click();
  await expect(page).toHaveURL('/');
});

test('после сетевой ошибки каталог можно повторно загрузить', async ({ page }) => {
  let recovered = false;
  await page.route('**/api/v1/catalog/models?*', async route => {
    if (!recovered) {
      await route.fulfill({ status: 503, body: '{}' });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' });
    }
  });
  await page.goto('/catalog/models');
  await expect(page.getByRole('alert')).toBeVisible();
  recovered = true;
  await page.getByRole('button', { name: 'Повторить запрос' }).click();
  await expect(page.getByText('Пока нет опубликованных моделей.')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('личный кабинет и staff закрыты без серверного допуска', async ({ page }) => {
  await page.route('**/api/v1/dev/staff/access', route => route.fulfill({
    status: 401, contentType: 'application/json', body: '{"code":"UNAUTHENTICATED"}'
  }));
  for (const path of ['/account', '/staff']) {
    await page.goto(path);
    if (path === '/account') await expect(page.getByRole('alert')).toContainText('403 — доступ запрещён');
    else await expect(page.getByRole('alert')).toContainText('401 — требуется локальный вход');
    await expect(page.locator('form')).toHaveCount(0);
  }
});

test('мобильная навигация доступна с клавиатуры на 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('navigation', { name: 'Разделы сервиса' })).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  await page.getByRole('navigation').getByRole('link', { name: 'Личный кабинет' }).focus();
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Личный кабинет' })).toBeFocused();
});

test('тема сохраняется после перезагрузки и перехода, без auth данных в storage', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('dark');
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Тема оформления' })).toHaveValue('dark');
  await expect(page.locator('main')).toHaveClass(/dark/);
  await page.getByRole('navigation').getByRole('link', { name: 'Бренды' }).click();
  await expect(page.getByRole('combobox', { name: 'Тема оформления' })).toHaveValue('dark');
  const stored = await page.evaluate(() => Object.keys(localStorage));
  expect(stored).toEqual(['roboport-theme']);
});

test('React Router сохраняет SPA навигацию и историю браузера', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('navigation').getByRole('link', { name: 'Бренды' }).click();
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Бренды' })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('navigation').getByRole('link', { name: 'Модели' }).click();
  await page.goBack();
  await expect(page).toHaveURL(/\/catalog\/brands$/);
  await expect(page.getByRole('heading', { name: 'Бренды роботов' })).toBeVisible();
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Бренды' })).toHaveAttribute('aria-current', 'page');
});

test('приёмочные скриншоты desktop/mobile и 401-заглушек', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Сервис обслуживания роботов' })).toBeVisible();
  await page.screenshot({ path: 'test-results/roboport-home-desktop.png', fullPage: true });
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('dark');
  await page.screenshot({ path: 'test-results/roboport-home-dark.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/account');
  await expect(page.getByRole('alert')).toContainText('403 — доступ запрещён');
  await page.screenshot({ path: 'test-results/roboport-account-mobile.png', fullPage: true });
});


test('синтетические модели: фильтр, cursor, карточка и 404', async ({page})=>{
 const requests:string[]=[];
 await page.route('**/api/v1/catalog/models?*',async route=>{
  const u=new URL(route.request().url()); requests.push(u.search);
  const next=u.searchParams.get('cursor');
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(next?
    {items:[{id:'demo-2',name:'Синтетическая модель 2',supportStatus:'candidate'}]}:
    {items:[{id:'demo-1',name:'Синтетическая модель 1',supportStatus:'candidate'}],nextCursor:'next-demo'})});
 });
 await page.route('**/api/v1/catalog/models/demo-1',r=>r.fulfill({status:200,contentType:'application/json',
  body:JSON.stringify({id:'demo-1',name:'Синтетическая модель 1',category:'vacuum',supportStatus:'candidate'})}));
 await page.route('**/api/v1/catalog/models/missing',r=>r.fulfill({status:404,contentType:'application/json',
  body:JSON.stringify({code:'NOT_FOUND',message:'Нет модели',requestId:'00000000-0000-0000-0000-000000000001'})}));
 await page.goto('/catalog/models');
 await expect(page.getByText('Поддержка не подтверждена')).toBeVisible();
 await page.getByRole('button',{name:'Далее'}).click();
 await expect(page.getByText('Синтетическая модель 2')).toBeVisible();
 await page.getByRole('button',{name:'Назад'}).click();
 await page.getByRole('link',{name:'Синтетическая модель 1'}).click();
 await expect(page.getByRole('heading',{name:'Синтетическая модель 1'})).toBeVisible();
 await expect(page.getByText('Статус поддержки: Не подтверждён')).toBeVisible();
 await page.goto('/catalog/models/missing');
 await expect(page.getByRole('alert')).toContainText('404');
 expect(requests.some(q=>q.includes('cursor=next-demo'))).toBe(true);
});

test('фильтр каталога и 390px + Auto не вызывают реальный intake',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/v1/catalog/models?*',r=>r.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'}));
 await page.goto('/catalog/models');
 await page.getByRole('textbox',{name:'Фильтр бренда'}).fill('demo-brand');
 await expect(page).toHaveURL(/brandId=demo-brand/);
 await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('auto');
 await expect(page.getByText('Пока нет опубликованных моделей.')).toBeVisible();
 const width=await page.evaluate(()=>document.documentElement.scrollWidth);
 expect(width).toBeLessThanOrEqual(390);
 await expect(page.getByText('Демонстрационный каталог. Реальные обращения пока не принимаются.')).toBeVisible();
});
