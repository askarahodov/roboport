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
  const search = page.locator('.sidebar .search input');
  await search.fill('доказательство');
  await expect(page.locator('.sidebar .results-panel')).toBeVisible();
  const results = page.locator('.sidebar .results-panel');
  await expect(results.locator('a').first()).toBeVisible();
  await expect(results).not.toContainText('Ничего не найдено');
});

test('пользовательские разделы явно разделяют planned и текущий dev-функционал', async ({ page }) => {
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
    // Клиент, мастер-приёмщик и менеджер теперь имеют ограниченный dev-сценарий.
    // Остальные роли всё ещё содержат только planned-функции.
    await expect(page.locator('.markdown-section')).toContainText(
      ['client', 'intake', 'manager'].includes(path)
        ? 'Статус: частично current в синтетическом dev'
        : 'Статус: planned',
    );
    await expect(page.locator('.markdown-section')).not.toContainText(
      '404 - Not found',
    );
  }
});

test('относительные ссылки внутри вложенного руководства ведут к существующей странице', async ({ page }) => {
  await page.goto('/docs/#/guides/client');
  await expect(page.locator('.markdown-section h1')).toContainText('Клиент автосервиса');
  await page.locator('.markdown-section a', { hasText: 'Описание QA-сценария' }).click();
  await expect(page.locator('.markdown-section h1')).toContainText(
    'Привязка автомобиля к клиенту',
  );
});

test('на мобильной ширине открывается меню и отображается широкое содержимое', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/docs/');
  await expect(page.locator('.markdown-section h1')).toBeVisible({ timeout: 25_000 });
  await page.locator('.sidebar-toggle').click();
  await expect(page.locator('.sidebar-nav a[href*="guides/client"]')).toBeVisible();
  await page.locator('.sidebar-nav a[href*="guides/client"]').click();
  await expect(page.locator('.markdown-section h1')).toContainText('Клиент автосервиса');
  await expect(page.locator('.markdown-section')).toContainText('Статус: частично current в синтетическом dev');
});

test('журнал dev-проверок доступен из Docsify sidebar и раскрывает реальный статус', async ({ page }) => {
  await page.goto('/docs/');
  await page.locator('.sidebar-nav a[href*="operations/DOCS_VERSIONS"]').click();
  await expect(page.locator('.markdown-section h1')).toContainText('Версии базы знаний');
  await expect(page.locator('.markdown-section')).toContainText('Git SHA');
  await expect(page.locator('.markdown-section')).toContainText('не является');
  await expect(page.locator('.markdown-section')).toContainText('Не завершено');
  await expect(page.locator('.markdown-section')).not.toContainText('404 - Not found');
  await page.locator('.markdown-section a', { hasText: 'политику документации' }).click();
  await expect(page.locator('.markdown-section h1')).toContainText('Политика документации');
});
