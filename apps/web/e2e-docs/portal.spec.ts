import { test, expect } from '@playwright/test';

test('главная, навигация, кодовые блоки и поиск', async ({ page }) => {
  await page.goto('/docs/');
  await expect(page.locator('.markdown-section h1')).toContainText(
    'База знаний Airmatik Pro Service',
    { timeout: 25_000 },
  );
  await expect(page.locator('.sidebar')).toBeVisible();
  await expect(page.locator('.sidebar-nav')).toContainText('Мастер-приёмщик');
  await expect(page.locator('.sidebar .search input')).toBeVisible();

  await page.locator('.sidebar-nav a[href*="auto/VEHICLE_OWNERSHIP"]').click();
  await expect(page.locator('.markdown-section h1')).toContainText(
    'Привязка автомобиля к клиенту',
  );
  await expect(page.locator('.markdown-section pre code').first()).toBeVisible();
});

test('все пользовательские разделы существуют и явно обозначены как planned', async ({ page }) => {
  test.setTimeout(100_000);
  const routes = [
    ['administrator', 'Администратор'],
    ['intake', 'Мастер-приёмщик'],
    ['mechanic', 'Механик и диагност'],
    ['warehouse', 'Кладовщик и закупщик'],
    ['manager', 'Руководитель'],
    ['finance', 'Бухгалтер и кассир'],
    ['client', 'Клиент автосервиса'],
    ['mobile', 'Мобильное приложение'],
  ];
  await page.goto('/docs/');
  for (const [path, heading] of routes) {
    await page.locator('.sidebar-nav a[href*="guides/' + path + '"]').click();
    await expect(page.locator('.markdown-section h1')).toContainText(heading);
    await expect(page.locator('.markdown-section')).toContainText(
      'Статус: planned',
    );
    await expect(page.locator('.markdown-section')).not.toContainText(
      '404 - Not found',
    );
  }
});

test('на мобильной ширине открывается меню и отображается широкое содержимое', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/docs/');
  await expect(page.locator('.markdown-section h1')).toBeVisible({ timeout: 25_000 });
  await page.locator('.sidebar-toggle').click();
  await expect(page.locator('.sidebar-nav a[href*="guides/client"]')).toBeVisible();
  await page.locator('.sidebar-nav a[href*="guides/client"]').click();
  await expect(page.locator('.markdown-section h1')).toContainText('Клиент автосервиса');
  await expect(page.locator('.markdown-section')).toContainText('Статус: planned');
});
