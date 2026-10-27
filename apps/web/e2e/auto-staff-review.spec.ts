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
