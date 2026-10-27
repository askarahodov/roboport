import { test, expect } from '@playwright/test';

const vehiclesUrl = '**/api/v1/dev/auto/vehicles?*';
const identity = '00000000-0000-4000-8000-000000000001';

async function authorized(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ subjectId: identity }),
  }));
}

test('автомобили нельзя загрузить без подтверждённой серверной сессии', async ({ page }) => {
  let fetches = 0;
  await page.route(vehiclesUrl, route => { fetches++; return route.fulfill({
    status: 200, body: '{"items":[]}', contentType: 'application/json',
  }); });
  await page.goto('/account/vehicles');
  await expect(page.getByRole('alert')).toContainText('403 — доступ запрещён');
  await expect(page.getByRole('navigation', { name: 'Разделы личного кабинета' })).toHaveCount(0);
  expect(fetches).toBe(0);
});

test('автомобили dev: безопасная маска VIN, светлая/тёмная темы и адаптивность', async ({ page }) => {
  await authorized(page);
  await page.route(vehiclesUrl, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [{
      id: '11111111-1111-4111-8111-111111111111',
      make: 'SyntheticMake', model: 'TestCar',
      variant: 'Тест', modelYear: 2026, vinLast4: '1234',
    }] }),
  }));
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/account/vehicles');
    await expect(page.getByRole('heading', { name: 'Автомобили (dev)' })).toBeVisible();
    await expect(page.getByText('SyntheticMake TestCar')).toBeVisible();
    await expect(page.getByText('VIN ••••1234')).toBeVisible();
    await expect(page.getByText('Тестовый режим Airmatik', { exact: false })).toBeVisible();
    for (const theme of ['light', 'dark', 'auto']) {
      await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption(theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    }
    const nav = page.getByRole('navigation', { name: 'Разделы личного кабинета' });
    await expect(nav.getByRole('link', { name: 'Автомобили (dev)' })).toBeVisible();
    const refresh = page.getByRole('button', { name: 'Обновить автомобили' });
    await refresh.focus();
    await expect(refresh).toBeFocused();
  }
});

test('машина pending скрыта, ошибка восстанавливается после повтора', async ({ page }) => {
  await authorized(page);
  // React StrictMode может отменять и повторять стартовый fetch.
  // Сбой моделируется состоянием сервера до явного действия пользователя.
  let recovered = false;
  await page.route(vehiclesUrl, route => route.fulfill(
    recovered
      ? { status: 200, contentType: 'application/json', body: '{"items":[]}' }
      : { status: 503, contentType: 'application/json', body: '{}' },
  ));
  await page.goto('/account/vehicles');
  await expect(page.getByRole('alert')).toContainText('Не удалось получить список');
  recovered = true;
  await page.getByRole('button', { name: 'Обновить автомобили' }).click();
  await expect(page.getByText('Подтверждённых автомобилей пока нет.')).toBeVisible();
  await expect(page.getByText('SyntheticMake TestCar')).toHaveCount(0);
});

test('403/404 не превращаются в фальшивый пустой список', async ({ page }) => {
  await authorized(page);
  await page.route(vehiclesUrl, route => route.fulfill({
    status: 403, contentType: 'application/json', body: '{}',
  }));
  await page.goto('/account/vehicles');
  await expect(page.getByText('Доступ к автомобилям не предоставлен', { exact: false })).toBeVisible();
  await expect(page.getByText('Подтверждённых автомобилей пока нет.')).toHaveCount(0);
});
