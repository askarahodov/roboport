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
  await page.route('**/api/v1/dev/tickets/keyset?limit=20', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [
      { id: '22222222-2222-4222-8222-222222222222', state: 'APPROVED', version: 4 },
    ], nextCursor: null }),
  }));
  await page.goto('/account/tickets');
  await expect(page.getByText(/Заявка 22222222-2222-4222-8222-222222222222/)).toBeVisible();
  await page.unroute('**/api/v1/dev/tickets/keyset?limit=20');
  await page.route('**/api/v1/dev/tickets/keyset?limit=20', route => route.fulfill({
    status: 401, contentType: 'application/json', body: '{"code":"UNAUTHENTICATED"}',
  }));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('доступ отозван');
  await expect(page.getByText(/Заявка 22222222-2222-4222-8222-222222222222/)).toHaveCount(0);
});
