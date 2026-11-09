import { test, expect } from '@playwright/test';

test('автомобильная главная и маршруты — без робототехнического каталога', async ({ page }) => {
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Сервисное обслуживание автомобилей' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Путь автомобиля через СТО' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Запись на сервис/ }).first()).toHaveAttribute('href', '/account/auto-booking');
    await expect(page.getByText('Только синтетический стенд разработки', { exact: false })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Разделы сервиса' }).getByRole('link', { name: 'Личный кабинет' })).toBeVisible();
    await expect(page.getByText(/роботов|робототехники|Модели роботов/i)).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});

test('старые публичные URL не раскрывают каталоги и заявки', async ({ page }) => {
  for (const path of ['/robots', '/catalog/brands', '/catalog/models', '/catalog/models/example', '/request-demo', '/account/claim']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Страница не найдена' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Сервисное обслуживание автомобилей' })).toHaveCount(0);
  }
});

test('закрытые приватные разделы не возвращаются через wildcard route', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"subjectId":"synthetic-customer"}',
  }));
  for (const path of ['/account/robots', '/account/tickets', '/account/estimates', '/account/documents']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Раздел не найден' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
  }
});

test('навигация, тема и история SPA работают после замены продукта', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('dark');
  const navigation = page.getByRole('navigation', { name: 'Разделы сервиса' });
  await navigation.getByRole('link', { name: 'Личный кабинет' }).click();
  await expect(page).toHaveURL(/\/account$/);
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Сервисное обслуживание автомобилей' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Тема оформления' })).toHaveValue('dark');
  await expect(page.locator('main')).toHaveClass(/dark/);
  const stored = await page.evaluate(() => Object.keys(localStorage));
  expect(stored).toEqual(['roboport-theme']);
});

test('закрытый доступ не выводит приватные данные', async ({ page }) => {
  await page.route('**/api/v1/dev/staff/access', route => route.fulfill({
    status: 401, contentType: 'application/json', body: '{"code":"UNAUTHENTICATED"}',
  }));
  await page.goto('/staff');
  await expect(page.getByRole('alert')).toContainText('401');
  await expect(page.getByRole('navigation', { name: 'Навигация СТО' })).toHaveCount(0);
});
