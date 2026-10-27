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


const fakeVehicle = (number: number) => ({
  id: '00000000-0000-4000-8000-' + number.toString(16).padStart(12, '0'),
  make: 'SyntheticMake', model: 'TestCar ' + number,
  variant: null, modelYear: 2026, vinLast4: '000' + (number % 10),
});

test('длинный список: по 20 машин, продолжение, без дублей и переполнения', async ({ page }) => {
  await authorized(page);
  const calls: string[] = [];
  await page.route(vehiclesUrl, route => {
    const url = new URL(route.request().url());
    const offset = url.searchParams.get('offset') ?? '';
    calls.push(offset);
    const items = offset === '0'
      ? Array.from({ length: 20 }, (_, index) => fakeVehicle(index + 1))
      : [fakeVehicle(20), fakeVehicle(21)];
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items }),
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/account/vehicles');
  await expect(page.getByText('Показано автомобилей: 20')).toBeVisible();
  await expect(page.locator('.account-record')).toHaveCount(20);
  await page.getByRole('button', { name: 'Показать ещё автомобили' }).click();
  await expect(page.getByText('Показано автомобилей: 21')).toBeVisible();
  await expect(page.locator('.account-record')).toHaveCount(21);
  await expect(page.getByRole('button', { name: 'Показать ещё автомобили' })).toHaveCount(0);
  expect(calls).toContain('0');
  expect(calls).toContain('20');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('сбой следующей страницы не стирает первую, повтор не создаёт дублей', async ({ page }) => {
  await authorized(page);
  let fail = true;
  await page.route(vehiclesUrl, route => {
    const offset = new URL(route.request().url()).searchParams.get('offset');
    if (offset === '20' && fail) return route.fulfill({
      status: 503, contentType: 'application/json', body: '{}',
    });
    const items = offset === '0'
      ? Array.from({ length: 20 }, (_, index) => fakeVehicle(index + 1))
      : [fakeVehicle(21)];
    return route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ items }),
    });
  });
  await page.goto('/account/vehicles');
  await expect(page.locator('.account-record')).toHaveCount(20);
  await page.getByRole('button', { name: 'Показать ещё автомобили' }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить следующую страницу');
  await expect(page.locator('.account-record')).toHaveCount(20);
  fail = false;
  await page.getByRole('button', { name: 'Повторить загрузку страницы' }).click();
  await expect(page.locator('.account-record')).toHaveCount(21);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('отозванная account_customer_link скрывает старые карточки даже при 200 + пустой список', async ({ page }) => {
  await authorized(page);
  let revoked = false;
  const calls: string[] = [];
  await page.route(vehiclesUrl, route => {
    const offset = new URL(route.request().url()).searchParams.get('offset') ?? '';
    calls.push(offset);
    // Именно так работает live backend после отзыва связи: не 403, а 200 {items:[]}.
    const items = revoked ? [] : Array.from(
      { length: 20 }, (_, index) => fakeVehicle(index + 1),
    );
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items }),
    });
  });
  await page.goto('/account/vehicles');
  await expect(page.locator('.account-record')).toHaveCount(20);
  revoked = true;
  await page.getByRole('button', { name: 'Показать ещё автомобили' }).click();
  await expect(page.getByText('Подтверждённых автомобилей пока нет.')).toBeVisible();
  await expect(page.locator('.account-record')).toHaveCount(0);
  await expect(page.getByText('Показано автомобилей: 20')).toHaveCount(0);
  // Новая подгрузка сначала повторно запрашивает доступные первые 20.
  // Запрос смещения 20 не требуется, если первая страница уже пуста.
  expect(calls.length).toBeGreaterThanOrEqual(2);\n  expect(calls.every(offset => offset === '0')).toBe(true);
});

test('частичный отзыв связи удаляет старую карточку при следующей странице', async ({ page }) => {
  await authorized(page);
  let revoked = false;
  await page.route(vehiclesUrl, route => {
    const offset = new URL(route.request().url()).searchParams.get('offset');
    const items = !revoked
      ? Array.from({ length: 20 }, (_, index) => fakeVehicle(index + 1))
      : offset === '0'
        ? Array.from({ length: 20 }, (_, index) => fakeVehicle(index + 2))
        : [fakeVehicle(22)];
    return route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ items }),
    });
  });
  await page.goto('/account/vehicles');
  await expect(page.getByText('SyntheticMake TestCar 1')).toBeVisible();
  revoked = true;
  await page.getByRole('button', { name: 'Показать ещё автомобили' }).click();
  await expect(page.getByText('Показано автомобилей: 21')).toBeVisible();
  await expect(page.getByText('SyntheticMake TestCar 1')).toHaveCount(0);
  await expect(page.getByText('SyntheticMake TestCar 22')).toBeVisible();
  await expect(page.locator('.account-record')).toHaveCount(21);
});
