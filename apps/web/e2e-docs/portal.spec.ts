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

test('390px и 1280px: Docsify контраст AA, dark/light и отсутствие горизонтального overflow', async ({ page }) => {
  test.setTimeout(120_000);
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/docs/#/auto/VEHICLE_OWNERSHIP');
      await expect(page.locator('.markdown-section h1')).toContainText(
        'Привязка автомобиля к клиенту', { timeout: 25_000 },
      );
      if (width === 390) {
        await page.locator('.sidebar-toggle').click();
      }
      await expect(page.locator('.sidebar-nav a[href*="guides/client"]')).toBeVisible();
      await expect(page.locator('.markdown-section pre code').first()).toBeVisible();

      // Compute actual browser style, not a hard-coded design token. Code blocks
      // inherit their opaque background from the nearest ancestor (<pre>).
      const samples = await page.evaluate(() => {
        const selectors = [
          '.markdown-section h1', '.markdown-section a',
          '.markdown-section pre code', '.sidebar-nav a[href*="guides/client"]',
          '.sidebar .search input',
        ];
        const rgb = (css: string) => {
          const digits = css.match(/[\d.]+/g)?.map(Number) ?? [];
          return [digits[0] ?? 0, digits[1] ?? 0, digits[2] ?? 0];
        };
        const luminance = (css: string) => rgb(css)
          .map(v => v / 255)
          .map(v => v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4))
          .reduce((sum, channel, i) => sum + channel * [0.2126, 0.7152, 0.0722][i], 0);
        const opaqueBg = (element: Element) => {
          let node: Element | null = element;
          while (node) {
            const color = getComputedStyle(node).backgroundColor;
            if (color && color !== 'transparent' && !color.endsWith(', 0)')) return color;
            node = node.parentElement;
          }
          return getComputedStyle(document.body).backgroundColor;
        };
        return selectors.map(selector => {
          const element = document.querySelector(selector);
          if (!element) return { selector, ratio: 0, foreground: 'absent', background: 'absent', preBackground: 'absent' };
          const foreground = getComputedStyle(element).color;
          const background = opaqueBg(element);
          const preBackground = getComputedStyle(element.closest('pre') ?? element).backgroundColor;
          const fg = luminance(foreground);
          const bg = luminance(background);
          return {
            selector, foreground, background, preBackground,
            ratio: (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05),
          };
        });
      });
      for (const { selector, ratio, foreground, background, preBackground } of samples) {
        expect(ratio,
          `WCAG AA ${selector} ${scheme}/${width}px; foreground=${foreground}, opaqueBg=${background}, preBg=${preBackground}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
      const widthReport = await page.evaluate(() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
      }));
      expect(widthReport.document, `no root overflow ${scheme}/${width}`).toBeLessThanOrEqual(
        widthReport.viewport + 2,
      );
    }
  }
});

test('навигация Docsify полностью доступна с клавиатуры и показывает видимый фокус', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/docs/');
  await expect(page.locator('.sidebar-nav')).toBeVisible({ timeout: 25_000 });
  let found = false;
  for (let i = 0; i < 65; i++) {
    await page.keyboard.press('Tab');
    found = await page.evaluate(() => !!document.activeElement?.matches('.sidebar-nav a'));
    if (found) break;
  }
  expect(found, 'sidebar nav is reachable without mouse').toBe(true);
  const focus = page.locator('.sidebar-nav a:focus-visible');
  await expect(focus).toHaveCount(1);
  const outline = await focus.evaluate(el => ({
    width: parseFloat(getComputedStyle(el).outlineWidth),
    style: getComputedStyle(el).outlineStyle,
  }));
  expect(outline.style).toBe('solid');
  expect(outline.width).toBeGreaterThanOrEqual(2);
  await page.keyboard.press('Enter');
  await expect(page.locator('.markdown-section h1')).toBeVisible();
  await expect(page.locator('.markdown-section')).not.toContainText('404 - Not found');
});
