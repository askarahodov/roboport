import { test, expect } from '@playwright/test';

const org = '11111111-1111-4111-8111-111111111111';
const user = '22222222-2222-4222-8222-222222222222';
const grant = '33333333-3333-4333-8333-333333333333';
const loc = '44444444-4444-4444-8444-444444444444';
const endpoint = '**/api/v1/dev/auto/organizations/*/staff*';

test('admin console denies access and does not render staff identifiers after 403', async ({ page }) => {
  await page.route(endpoint, route => route.fulfill({
    status: 403, contentType: 'application/json', body: '{"code":"FORBIDDEN"}',
  }));
  await page.goto('/staff/auto-admin');
  await expect(page.getByRole('heading', { name: 'Сотрудники организации' })).toBeVisible();
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(org);
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('button', { name: 'Назначить' })).toHaveCount(0);
  await expect(page.getByText(user)).toHaveCount(0);
});

test('admin console creates and revokes only the selected tenant grant', async ({ page }) => {
  let active = false;
  const posts: { path: string; payload: unknown }[] = [];
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(endpoint, route => {
    const req = route.request();
    if (req.method() === 'GET') return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items: active
        ? [{ grantId: grant, userId: user, role: 'INTAKE', locationId: loc }] : [] }),
    });
    expect(req.method()).toBe('POST');
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    posts.push({ path: new URL(req.url()).pathname, payload: req.postDataJSON() });
    if (req.url().endsWith('/revoke')) {
      active = false;
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: '{"status":"REVOKED"}' });
    }
    active = true;
    return route.fulfill({ status: 201, contentType: 'application/json',
      body: JSON.stringify({ grantId: grant }) });
  });
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(org);
  await expect(page.getByText('Активных назначений нет.')).toBeVisible();
  await page.getByRole('textbox', { name: 'UUID пользователя' }).fill(user);
  await page.getByRole('textbox', { name: 'UUID филиала (необязательно)' }).fill(loc);
  await page.getByRole('button', { name: 'Назначить' }).click();
  await expect(page.getByText('Назначение создано.')).toBeVisible();
  await expect(page.getByText(user)).toBeVisible();
  await page.getByRole('button', { name: 'Отозвать', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Подтвердить отзыв' })).toBeVisible();
  expect(posts).toHaveLength(1);
  await page.getByRole('button', { name: 'Подтвердить отзыв' }).click();
  await expect(page.getByText('Доступ сотрудника отозван.')).toBeVisible();
  await expect(page.getByText('Активных назначений нет.')).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[0]).toEqual({
    path: '/api/v1/dev/auto/organizations/' + org + '/staff',
    payload: { userId: user, role: 'INTAKE', locationId: loc },
  });
  expect(posts[1]).toEqual({
    path: '/api/v1/dev/auto/organizations/' + org + '/staff/' + grant + '/revoke',
    payload: {},
  });
});

test('admin console handles 409 without exposing a false success state', async ({ page }) => {
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(endpoint, route => route.fulfill({
    status: route.request().method() === 'GET' ? 200 : 409,
    contentType: 'application/json',
    body: route.request().method() === 'GET' ? '{"items":[]}' : '{"code":"CONFLICT"}',
  }));
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(org);
  await expect(page.getByText('Активных назначений нет.')).toBeVisible();
  await page.getByRole('textbox', { name: 'UUID пользователя' }).fill(user);
  await page.getByRole('button', { name: 'Назначить' }).click();
  await expect(page.getByRole('status')).toContainText('409');
  await expect(page.getByText('Назначение создано.')).toHaveCount(0);
});

test('mobile staff console remains within viewport in dark mode', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route(endpoint, route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"items":[]}',
  }));
  await page.goto('/staff/auto-admin');
  await page.getByRole('combobox', { name: 'Тема оформления' }).selectOption('dark');
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(org);
  await expect(page.getByText('Активных назначений нет.')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
