import { test, expect } from '@playwright/test';

const org = '11111111-1111-4111-8111-111111111111';
const user = '22222222-2222-4222-8222-222222222222';
const grant = '33333333-3333-4333-8333-333333333333';
const loc = '44444444-4444-4444-8444-444444444444';
// Playwright * does not match nested /staff/{grantId}/revoke paths.
const endpoint = '**/api/v1/dev/auto/organizations/**';

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
  await expect.poll(() => posts.length, { message: 'revoke must submit exactly one POST' }).toBe(2);
  await expect(page.getByRole('status').filter({hasText:'Доступ сотрудника отозван.'})).toBeVisible();
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


test('organization administration entry is discoverable in main navigation on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route(endpoint, route => route.fulfill({
    status: 403, contentType: 'application/json', body: '{"code":"FORBIDDEN"}',
  }));
  await page.goto('/');
  const navigation = page.getByRole('navigation', { name: 'Разделы сервиса' });
  const link = navigation.getByRole('link', { name: 'Организации (dev)' });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/staff\/auto-admin$/);
  await expect(page.getByRole('heading', { name: 'Сотрудники организации' })).toBeVisible();
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(org);
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('switching organizations clears previous success and active staff data', async ({ page }) => {
  const otherOrg = '55555555-5555-4555-8555-555555555555';
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(endpoint, route => {
    if (route.request().method() !== 'GET') return route.fulfill({
      status: 201, contentType: 'application/json', body: JSON.stringify({ grantId: grant }),
    });
    if (route.request().url().includes(otherOrg)) return route.fulfill({
      status: 403, contentType: 'application/json', body: '{"code":"FORBIDDEN"}',
    });
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items: [{ grantId: grant, userId: user, role: 'INTAKE', locationId: null }] }),
    });
  });
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(org);
  await expect(page.getByText(user)).toBeVisible();
  await page.getByRole('textbox', { name: 'UUID пользователя' }).fill(user);
  await page.getByRole('button', { name: 'Назначить' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Назначение создано.' })).toBeVisible();
  await page.getByRole('textbox', { name: 'UUID организации' }).fill(otherOrg);
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText(user)).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'Назначение создано.' })).toHaveCount(0);
});


test('ADMIN sees tenant-scoped grant and revoke audit and clears data on tenant switch', async ({ page }) => {
  const other = '55555555-5555-4555-8555-555555555555';
  const actor = '66666666-6666-4666-8666-666666666666';
  const eventA = '77777777-7777-4777-8777-777777777777';
  const eventB = '88888888-8888-4888-8888-888888888888';
  await page.route(endpoint, route => {
    const url = route.request().url();
    if (url.includes(other)) return route.fulfill({
      status: 403, contentType: 'application/json', body: '{"code":"FORBIDDEN"}',
    });
    if (url.includes('/audit')) return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({items: [
        {eventId:eventA, grantId:grant, actorId:actor, userId:user,
          action:'GRANTED', role:'INTAKE', locationId:loc, createdAt:'2026-10-30T10:00:00Z'},
        {eventId:eventB, grantId:grant, actorId:actor, userId:user,
          action:'REVOKED', role:'INTAKE', locationId:loc, createdAt:'2026-10-30T10:01:00Z'},
      ]}),
    });
    return route.fulfill({
      status: 200, contentType: 'application/json', body: '{"items":[]}',
    });
  });
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox', {name:'UUID организации'}).fill(org);
  await expect(page.getByRole('button', {name:'Показать журнал'})).toBeVisible();
  await page.getByRole('button', {name:'Показать журнал'}).click();
  const audit = page.getByRole('region', {name:'Журнал действий сотрудников'});
  await expect(audit.getByText('Назначение', {exact:false})).toBeVisible();
  await expect(audit.getByText('Отзыв', {exact:false})).toBeVisible();
  await expect(audit.getByText(actor, {exact:false}).first()).toBeVisible();
  await page.getByRole('textbox', {name:'UUID организации'}).fill(other);
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(audit).toHaveCount(0);
  await expect(page.getByText(actor)).toHaveCount(0);
});

test('ADMIN audit rejects remote 403 and malformed rows without showing identifiers', async ({ page }) => {
  let malformed = false;
  await page.route(endpoint, route => {
    if (route.request().url().includes('/audit')) return route.fulfill({
      status: malformed ? 200 : 403, contentType: 'application/json',
      body: malformed ? '{"items":[{"userId":"private"}]}' : '{"code":"FORBIDDEN"}',
    });
    return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
  });
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox', {name:'UUID организации'}).fill(org);
  await page.getByRole('button', {name:'Показать журнал'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ к журналу запрещён');
  malformed = true;
  await page.getByRole('button', {name:'Показать журнал'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить журнал');
  await expect(page.getByText('private')).toHaveCount(0);
});
