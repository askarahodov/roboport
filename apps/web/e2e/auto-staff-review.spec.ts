import { test, expect } from '@playwright/test';

const vehicle = '11111111-1111-4111-8111-111111111111';
const customer = '22222222-2222-4222-8222-222222222222';
const link = '33333333-3333-4333-8333-333333333333';
const claim = '44444444-4444-4444-8444-444444444444';
const proof = 'SyntheticSecretOnlyInThisTest_DO_NOT_RECORD_2026';

async function staff(page: import('@playwright/test').Page, roles: string[]) {
  await page.route('**/api/v1/dev/staff/access', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ roles }),
  }));
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"local-test-csrf"}',
  }));
  await page.goto('/staff');
}

test('INTAKE создаёт pending, выдаёт одноразовый код, не видит менеджерское решение', async ({ page }) => {
  const calls: string[] = [];
  await page.route('**/api/v1/dev/auto/**', route => {
    const req = route.request();
    calls.push(req.url());
    expect(req.method()).toBe('POST');
    expect(req.headers()['x-csrf-token']).toBe('local-test-csrf');
    expect(req.headers()['content-type']).toContain('application/json');
    if (req.url().endsWith('/links')) {
      expect(req.postDataJSON()).toEqual({ customerId: customer });
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ linkId: link, status: 'PENDING' }) });
    }
    if (req.url().endsWith('/issue-proof')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ claimId: claim, proof }) });
    }
    throw Error('unexpected URL');
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await staff(page, ['INTAKE']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Автомобильные привязки (dev)' });
  await expect(region.getByRole('button', { name: 'Подтвердить связь' })).toHaveCount(0);
  await region.getByLabel('UUID автомобиля').fill(vehicle);
  await region.getByLabel('UUID клиента').fill(customer);
  await region.getByRole('button', { name: 'Создать ожидающую связь' }).click();
  await expect(region.getByText('Создана неподтверждённая связь', { exact: false })).toBeVisible();
  await expect(region.getByLabel('UUID связи', { exact: true })).toHaveValue(link);
  await region.getByRole('button', { name: 'Выдать код для клиента' }).click();
  await expect(region.getByText(proof, { exact: true })).toBeVisible();
  await expect(region.getByText(claim, { exact: true })).toBeVisible();
  await expect(region.getByText('Связь подтверждена', { exact: false })).toHaveCount(0);
  expect(calls.length).toBe(2);
  expect(calls.every(url => !url.includes(proof))).toBeTruthy();
  await region.getByRole('button', { name: 'Скрыть код' }).click();
  await expect(page.getByText(proof, { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('MANAGER может принять независимое решение и отозвать только с подтверждением', async ({ page }) => {
  const actions: {url:string; body:unknown}[] = [];
  await page.route('**/api/v1/dev/auto/**', route => {
    actions.push({ url: route.request().url(), body: route.request().postDataJSON() });
    if (route.request().url().endsWith('/review')) return route.fulfill({
      status: 200, contentType: 'application/json', body: '{"status":"VERIFIED"}',
    });
    if (route.request().url().endsWith('/revoke')) return route.fulfill({
      status: 200, contentType: 'application/json', body: '{"status":"REVOKED"}',
    });
    throw Error('unexpected mutation');
  });
  await staff(page, ['MANAGER']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Автомобильные привязки (dev)' });
  await expect(region.getByRole('button', { name: 'Выдать код для клиента' })).toHaveCount(0);
  await region.getByLabel('UUID подтверждения').fill(claim);
  await region.getByRole('button', { name: 'Подтвердить связь' }).click();
  await expect(region.getByText('Связь подтверждена независимым сотрудником.')).toBeVisible();
  await region.getByLabel('UUID связи для отзыва').fill(link);
  const revoke = region.getByRole('button', { name: 'Отозвать доступ' });
  await expect(revoke).toBeDisabled();
  await region.getByRole('checkbox', { name: 'Подтверждаю отзыв тестовой связи' }).check();
  await revoke.click();
  await expect(region.getByText('Связь отозвана. Доступ к автомобилю прекращён.')).toBeVisible();
  expect(actions).toHaveLength(2);
  expect(actions[0].body).toEqual({ approve: true });
  expect(actions[1].body).toEqual({});
});

test('403 от backend не раскрывает секрет и права роли недостаточно без object scope', async ({ page }) => {
  await page.route('**/api/v1/dev/auto/**', route => route.fulfill({
    status: 403, contentType: 'application/json', body: '{"code":"FORBIDDEN"}',
  }));
  await staff(page, ['INTAKE']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Автомобильные привязки (dev)' });
  await region.getByLabel('UUID автомобиля').fill(vehicle);
  await region.getByLabel('UUID клиента').fill(customer);
  await region.getByRole('button', { name: 'Создать ожидающую связь' }).click();
  await expect(region.getByRole('alert')).toContainText('Операция не выполнена');
  await expect(region.getByText('Одноразовый код:', { exact: false })).toHaveCount(0);
  await expect(region.getByRole('button', { name: 'Выдать код для клиента' })).toBeDisabled();
});

test('STOCK/AUDITOR не получают автомобильные действия и подсказки', async ({ page }) => {
  await staff(page, ['STOCK']);
  await expect(page.getByRole('button', { name: 'Автомобили (dev)' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Автомобильные привязки (dev)' })).toHaveCount(0);
});


test('CLIENT предъявляет proof только POST/CSRF, не получает право без решения MANAGER', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"subjectId":"55555555-5555-4555-8555-555555555555"}',
  }));
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"local-test-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/vehicles?*', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"items":[]}',
  }));
  let count = 0;
  await page.route('**/api/v1/dev/auto/claims/*/redeem', route => {
    count += 1;
    const request = route.request();
    expect(request.method()).toBe('POST');
    expect(request.headers()['x-csrf-token']).toBe('local-test-csrf');
    expect(request.postDataJSON()).toEqual({ proof });
    expect(request.url()).not.toContain(proof);
    return route.fulfill({
      status: 200, contentType: 'application/json', body: '{"status":"PENDING_REVIEW"}',
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/account/vehicles');
  const form = page.getByRole('form', { name: 'Подтверждение автомобиля (dev)' });
  await expect(form).toBeVisible();
  await form.getByLabel('UUID подтверждения').fill(claim);
  await form.getByLabel('Одноразовый код').fill(proof);
  await expect(form.getByLabel('Одноразовый код')).toHaveAttribute('type', 'password');
  await form.getByRole('button', { name: 'Предъявить код' }).click();
  await expect(form.getByRole('status')).toContainText('только после независимого решения');
  await expect(form.getByLabel('Одноразовый код')).toHaveValue('');
  await expect(page.getByText('Подтверждённых автомобилей пока нет.')).toBeVisible();
  expect(count).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('CLIENT не видит форму подтверждения после запрета чтения списка', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"subjectId":"55555555-5555-4555-8555-555555555555"}',
  }));
  await page.route('**/api/v1/dev/auto/vehicles?*', route => route.fulfill({
    status: 403, contentType: 'application/json', body: '{"code":"FORBIDDEN"}',
  }));
  await page.goto('/account/vehicles');
  await expect(page.getByText('Доступ к автомобилям не предоставлен', { exact: false })).toBeVisible();
  await expect(page.getByRole('form', { name: 'Подтверждение автомобиля (dev)' })).toHaveCount(0);
});

test('CLIENT ошибочный proof не подтверждает машину и очищает поле', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"subjectId":"55555555-5555-4555-8555-555555555555"}',
  }));
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"local-test-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/vehicles?*', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"items":[]}',
  }));
  await page.route('**/api/v1/dev/auto/claims/*/redeem', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"status":"REJECTED"}',
  }));
  await page.goto('/account/vehicles');
  const form = page.getByRole('form', { name: 'Подтверждение автомобиля (dev)' });
  await form.getByLabel('UUID подтверждения').fill(claim);
  await form.getByLabel('Одноразовый код').fill(proof);
  await form.getByRole('button', { name: 'Предъявить код' }).click();
  await expect(form.getByRole('alert')).toContainText('Код не принят');
  await expect(form.getByLabel('Одноразовый код')).toHaveValue('');
  await expect(page.getByText('Подтверждённых автомобилей пока нет.')).toBeVisible();
});


test('две роли: изменение UUID менеджерской заявки не меняет пару выданного кода', async ({ page }) => {
  const otherClaim = '66666666-6666-4666-8666-666666666666';
  await page.route('**/api/v1/dev/auto/**', route => {
    expect(route.request().url()).toContain('/issue-proof');
    return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ claimId: claim, proof }) });
  });
  await staff(page, ['INTAKE', 'MANAGER']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Автомобильные привязки (dev)' });
  await region.getByLabel('UUID связи', { exact: true }).fill(link);
  await region.getByRole('button', { name: 'Выдать код для клиента' }).click();
  await expect(region.getByText('UUID подтверждения:', { exact: false })).toContainText(claim);
  await expect(region.getByText(proof, { exact: true })).toBeVisible();
  await region.getByLabel('UUID подтверждения').fill(otherClaim);
  await expect(region.getByLabel('UUID подтверждения')).toHaveValue(otherClaim);
  await expect(region.getByText('UUID подтверждения:', { exact: false })).toContainText(claim);
  await expect(region.getByText(proof, { exact: true })).toBeVisible();
});


test('MANAGER делегирует только INTAKE с живым scope и отзывает назначение', async ({ page }) => {
  const target = '77777777-7777-4777-8777-777777777777';
  const assignment = '88888888-8888-4888-8888-888888888888';
  const seen: string[] = [];
  let active = false;
  await page.route('**/api/v1/dev/auto/vehicles/**', route => {
    const req = route.request();
    if (!req.url().includes('/intake-assignments')) throw Error('unexpected URL');
    if (req.method() === 'GET') return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items: active ? [{ assignmentId: assignment, userId: target }] : [] }),
    });
    expect(req.method()).toBe('POST');
    expect(req.headers()['x-csrf-token']).toBe('local-test-csrf');
    expect(req.headers()['content-type']).toContain('application/json');
    seen.push(req.url());
    if (req.url().endsWith('/revoke')) {
      expect(req.postDataJSON()).toEqual({});
      active = false;
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: '{"status":"REVOKED"}' });
    }
    expect(req.postDataJSON()).toEqual({ userId: target });
    active = true;
    return route.fulfill({
      status: 201, contentType: 'application/json',
      body: JSON.stringify({ assignmentId: assignment, status: 'ACTIVE' }),
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await staff(page, ['MANAGER']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Назначения приёмщиков (dev)' });
  await expect(region).toBeVisible();
  await region.getByLabel('UUID автомобиля для назначения').fill(vehicle);
  await region.getByLabel('UUID сотрудника с ролью INTAKE').fill(target);
  await region.getByRole('button', { name: 'Назначить приёмщика' }).click();
  await expect(region.getByText('Назначена только роль INTAKE', { exact: false })).toBeVisible();
  await region.getByRole('button', { name: 'Обновить назначения' }).click();
  await expect(region.getByText(target, { exact: true })).toBeVisible();
  await region.getByRole('button', { name: 'Отозвать назначение' }).click();
  await expect(region.getByText('Назначение отозвано', { exact: false })).toBeVisible();
  await expect(region.getByText(target, { exact: true })).toHaveCount(0);
  expect(seen).toHaveLength(2);
  expect(seen[0]).not.toContain('MANAGER');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('manager потерял scope: активные назначения скрываются после 404', async ({ page }) => {
  const target = '77777777-7777-4777-8777-777777777777';
  const assignment = '88888888-8888-4888-8888-888888888888';
  let revoked = false;
  await page.route('**/api/v1/dev/auto/vehicles/**', route => {
    if (!route.request().url().includes('/intake-assignments')) throw Error('unexpected');
    return route.fulfill({
      status: revoked ? 404 : 200, contentType: 'application/json',
      body: revoked ? '{"code":"NOT_FOUND"}' : JSON.stringify({
        items: [{ assignmentId: assignment, userId: target }],
      }),
    });
  });
  await staff(page, ['MANAGER']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Назначения приёмщиков (dev)' });
  await region.getByLabel('UUID автомобиля для назначения').fill(vehicle);
  await region.getByRole('button', { name: 'Обновить назначения' }).click();
  await expect(region.getByText(target, { exact: true })).toBeVisible();
  revoked = true;
  await region.getByRole('button', { name: 'Обновить назначения' }).click();
  await expect(region.getByText(target, { exact: true })).toHaveCount(0);
  await expect(region.getByRole('alert')).toContainText('проверьте права');
});

test('INTAKE без MANAGER не видит делегирование даже при известном UUID', async ({ page }) => {
  await staff(page, ['INTAKE']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  await expect(page.getByRole('region', { name: 'Назначения приёмщиков (dev)' })).toHaveCount(0);
});


test('INTAKE повторяет создание после сбоя сети с тем же ключом', async ({ page }) => {
  const keys: string[] = [];
  await page.route('**/api/v1/dev/auto/vehicles/*/links', route => {
    const key = route.request().headers()['idempotency-key'];
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    keys.push(key);
    return route.fulfill(keys.length === 1
      ? { status: 503, contentType: 'application/json', body: '{}' }
      : { status: 201, contentType: 'application/json',
        body: JSON.stringify({ linkId: link, status: 'PENDING' }) });
  });
  await staff(page, ['INTAKE']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const panel = page.getByRole('region', { name: 'Автомобильные привязки (dev)' });
  await panel.getByLabel('UUID автомобиля').fill(vehicle);
  await panel.getByLabel('UUID клиента').fill(customer);
  await panel.getByRole('button', { name: 'Создать ожидающую связь' }).click();
  await expect(panel.getByRole('alert')).toContainText('Операция не выполнена');
  await panel.getByRole('button', { name: 'Создать ожидающую связь' }).click();
  await expect(panel.getByText('Создана неподтверждённая связь', { exact: false })).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test('MANAGER повторяет grant после сбоя с тем же ключом, без duplicate', async ({ page }) => {
  const target = '77777777-7777-4777-8777-777777777777';
  const assignment = '88888888-8888-4888-8888-888888888888';
  const keys: string[] = [];
  await page.route('**/api/v1/dev/auto/vehicles/*/intake-assignments', route => {
    if (route.request().method() !== 'POST') throw Error('unexpected request');
    const key = route.request().headers()['idempotency-key'];
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    keys.push(key);
    return route.fulfill(keys.length === 1
      ? { status: 503, contentType: 'application/json', body: '{}' }
      : { status: 201, contentType: 'application/json',
        body: JSON.stringify({ assignmentId: assignment, status: 'ACTIVE' }) });
  });
  await staff(page, ['MANAGER']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const region = page.getByRole('region', { name: 'Назначения приёмщиков (dev)' });
  await region.getByLabel('UUID автомобиля для назначения').fill(vehicle);
  await region.getByLabel('UUID сотрудника с ролью INTAKE').fill(target);
  await region.getByRole('button', { name: 'Назначить приёмщика' }).click();
  await expect(region.getByRole('alert')).toContainText('Назначение отклонено');
  await region.getByRole('button', { name: 'Назначить приёмщика' }).click();
  await expect(region.getByText('Назначена только роль INTAKE', { exact: false })).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});


test('CLIENT предъявляет тот же proof с тем же ключом после потерянного ответа', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: '{"subjectId":"55555555-5555-4555-8555-555555555555"}',
  }));
  await page.route('**/api/v1/auth/csrf', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"csrfToken":"local-test-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/vehicles?*', route => route.fulfill({
    status: 200, contentType: 'application/json', body: '{"items":[]}',
  }));
  const keys: string[] = [];
  await page.route('**/api/v1/dev/auto/claims/*/redeem', route => {
    const request = route.request();
    keys.push(request.headers()['idempotency-key']);
    expect(request.postDataJSON()).toEqual({ proof });
    return route.fulfill(keys.length === 1
      ? { status: 503, contentType: 'application/json', body: '{}' }
      : { status: 200, contentType: 'application/json', body: '{"status":"PENDING_REVIEW"}' });
  });
  await page.goto('/account/vehicles');
  const form = page.getByRole('form', { name: 'Подтверждение автомобиля (dev)' });
  await form.getByLabel('UUID подтверждения').fill(claim);
  await form.getByLabel('Одноразовый код').fill(proof);
  await form.getByRole('button', { name: 'Предъявить код' }).click();
  await expect(form.getByRole('alert')).toContainText('Предъявление не удалось');
  await expect(form.getByLabel('Одноразовый код')).toHaveValue('');
  // Секрет не сохранён — клиент вводит его повторно.
  await form.getByLabel('Одноразовый код').fill(proof);
  await form.getByRole('button', { name: 'Предъявить код' }).click();
  await expect(form.getByRole('status')).toContainText('Код принят');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});

test('MANAGER retry проверки сохраняет ключ после HTTP 503', async ({ page }) => {
  const keys: string[] = [];
  await page.route('**/api/v1/dev/auto/claims/*/review', route => {
    keys.push(route.request().headers()['idempotency-key']);
    expect(route.request().postDataJSON()).toEqual({ approve: true });
    return route.fulfill(keys.length === 1
      ? { status: 503, contentType: 'application/json', body: '{}' }
      : { status: 200, contentType: 'application/json', body: '{"status":"VERIFIED"}' });
  });
  await staff(page, ['MANAGER']);
  await page.getByRole('button', { name: 'Автомобили (dev)' }).click();
  const panel = page.getByRole('region', { name: 'Автомобильные привязки (dev)' });
  await panel.getByLabel('UUID подтверждения').fill(claim);
  await panel.getByRole('button', { name: 'Подтвердить связь' }).click();
  await expect(panel.getByRole('alert')).toContainText('Операция не выполнена');
  await panel.getByRole('button', { name: 'Подтвердить связь' }).click();
  await expect(panel.getByText('Связь подтверждена независимым сотрудником.')).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
