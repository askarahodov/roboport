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
