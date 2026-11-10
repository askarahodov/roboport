import { test, expect } from '@playwright/test';
import { expectIndexedDocsSearch } from './docs-search-helper';

test('главная, навигация, кодовые блоки и поиск', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/docs/');
  await expect(page.locator('.markdown-section h1')).toContainText(
    'База знаний БоксРитм',
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
  await expectIndexedDocsSearch(page, 'доказательство');
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
    // Direct route navigation avoids Docsify sidebar re-render races when
    // traversing several long role pages; sidebar clicks have separate smoke.
    await page.goto('/docs/#/guides/' + path);
    await expect(page.locator('.markdown-section h1')).toContainText(heading);
    // Складская инструкция должна перечислять реализованные dev-операции
    // и явно запрещать работу с реальными данными; production ещё planned.
    if (path === 'warehouse') {
      await expect(page.locator('.markdown-section')).toContainText('Статус: current-dev synthetic');
      await expect(page.locator('.markdown-section')).toContainText('инвентаризации, прихода, выдачи/возврата и пересортицы');
      await expect(page.locator('.markdown-section')).toContainText('Запрещены реальные складские данные');
      await expect(page.locator('.markdown-section')).toContainText('/staff/warehouse-work-orders');
    } else {
      await expect(page.locator('.markdown-section')).toContainText(
        ['administrator','mechanic'].includes(path) ? 'Статус: частично current-dev'
          : ['client', 'intake', 'manager'].includes(path)
            ? 'Статус: частично current в синтетическом dev'
            : 'Статус: planned',
      );
    }
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
          // Chromium may serialize computed CSS colors as color(srgb 0..1)
          // instead of legacy rgb(0..255), especially on Windows.
          const srgb = css.match(/^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
          if (srgb) return [1, 2, 3].map(i => Number(srgb[i]) * 255);
          const legacy = css.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
          if (legacy) return [1, 2, 3].map(i => Number(legacy[i]));
          throw new Error(`Unrecognized computed CSS color: ${css}`);
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


test('руководство СТО, FAQ и legacy-архив не смешиваются в текущем меню',async({page})=>{
  test.setTimeout(90_000);
  await page.goto('/docs/');
  const sidebar=page.locator('.sidebar-nav');
  await expect(sidebar.locator('a[href*="guides/QUICK_START"]')).toBeVisible();
  await expect(sidebar.locator('a[href*="guides/FAQ"]')).toBeVisible();
  await expect(sidebar.locator('a[href*="guides/ACCESS_AND_TEST_MATRIX"]')).toBeVisible();
  await expect(sidebar).not.toContainText('TECHNICAL_SPEC');
  await sidebar.locator('a[href*="guides/QUICK_START"]').click();
  await expect(page.locator('.markdown-section h1')).toContainText('Быстрый старт по ролям');
  await expect(page.locator('.markdown-section')).toContainText('releaseAllowed');
  await page.goto('/docs/#/guides/FAQ');
  await expect(page.locator('.markdown-section h1')).toContainText('Частые вопросы');
  await expect(page.locator('.markdown-section')).toContainText('403');
  await page.goto('/docs/#/auto/LEGACY_ARCHIVE');
  await expect(page.locator('.markdown-section h1')).toContainText('Отдельный архив');
  await expect(page.locator('.markdown-section')).toContainText('не рабочая инструкция');
});


test('Docsify: поиск находит навигатор текущих задач и открывает его без несуществующих маршрутов', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/docs/');
  await expect(page.locator('.markdown-section h1')).toContainText('База знаний', { timeout: 25_000 });
  await expectIndexedDocsSearch(page, 'Навигатор операций автосервиса');
  const navigatorResult = page.locator('.sidebar .results-panel a[href*="ROLE_NAVIGATOR"]').first();
  await expect(navigatorResult).toBeVisible();
  await navigatorResult.click();
  await expect(page.locator('.markdown-section h1')).toContainText('Навигатор операций автосервиса');
  await expect(page.locator('.markdown-section')).toContainText('Отсутствующие в продукте операции');
  await expect(page.locator('.markdown-section')).not.toContainText('404 - Not found');
});

test('ролевой навигатор открывается с клавиатуры, ищется и различает planned/current-dev на 390/1280', async ({ page }) => {
  test.setTimeout(120_000);
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/docs/');
    await expect(page.locator('.markdown-section h1')).toContainText('База знаний');
    if (width === 390) await page.locator('.sidebar-toggle').click();
    const navigator = page.locator('.sidebar-nav a[href*="guides/ROLE_NAVIGATOR"]');
    await expect(navigator).toBeVisible();
    await navigator.focus();
    await expect(navigator).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('.markdown-section h1')).toContainText('Навигатор операций автосервиса');
    const content = page.locator('.markdown-section');
    await expect(content).toContainText('Самостоятельно записаться');
    await expect(content).toContainText('отзыв');
    await expect(content).toContainText('releaseAllowed: false');
    await expect(content).toContainText('planned для production');
    await expect(content).not.toContainText('404 - Not found');
    const pageWidth = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }));
    expect(pageWidth.scroll, 'navigator must not overflow viewport').toBeLessThanOrEqual(pageWidth.viewport + 2);
    await page.goto('/docs/#/guides/ROLE_NAVIGATOR');
    // Docsify retains the mobile sidebar across navigation, covering content.
    // Close it before activating a link from inside the rendered Markdown.
    if (width === 390) {
      const sidebar = page.locator('.sidebar');
      if (await sidebar.evaluate(el => el.classList.contains('show'))) {
        await page.locator('.sidebar-toggle').click();
      }
      await expect(sidebar).not.toHaveClass(/show/);
    }
    const matrixLink = page.locator('.markdown-section a', { hasText: 'Матрица доступов и E2E' });
    await expect(matrixLink).toBeVisible();
    await matrixLink.click();
    await expect(page.locator('.markdown-section h1')).toContainText('Матрица ролей');
  }
});

test('единая клиентская справка соответствует текущим пяти экранам БоксРитм', async ({ page }) => {
  test.setTimeout(90000);
  await page.setViewportSize({width:390,height:844});
  await page.goto('/docs/#/guides/client');
  const body=page.locator('.markdown-section');
  await expect(body.locator('h1')).toContainText('Клиент автосервиса');
  for (const route of ['/account/login','/account/vehicles','/account/auto-booking','/account/auto-orders','/account/settings']) {
    await expect(body).toContainText(route);
  }
  for (const action of ['Предъявить код','Показать свободные окна','Перенести свою запись',
    'Отменить свою запись','Подробнее о заказе','Согласовать последнюю версию',
    'Сохранить настройки','Повторить проверку']) {
    await expect(body).toContainText(action);
  }
  await expect(body).toContainText('planned');
  await expect(body).toContainText('БоксРитм');
  await expect(body.getByRole('link',{name:'FAQ'}).first()).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('ролевой Docsify хаб покрывает все рабочие роли и различает тестовый финансовый контур',async({page})=>{
  test.setTimeout(90000);
  await page.setViewportSize({width:390,height:844});
  await page.goto('/docs/#/guides/STAFF_TASKS');
  const body=page.locator('.markdown-section');
  await expect(body.locator('h1')).toContainText('Карта рабочих операций БоксРитм',{timeout:25000});
  for(const role of ['Администратор','Мастер-приёмщик','Механик и диагност',
    'Кладовщик и закупщик','Руководитель','Бухгалтер и кассир']){
    await expect(body).toContainText(role);
  }
  for(const route of ['/staff/auto-calendar','/staff/auto-diagnostics','/staff/auto-estimates',
    '/staff/auto-execution','/staff/auto-demo-finance-report','/staff/warehouse-work-orders']){
    await expect(body).toContainText(route);
  }
  await expect(body).toContainText('planned');
  await expect(body).toContainText('401');
  await expect(body).toContainText('403');
  await expect(body).toContainText('409');
  await expect(body).toContainText('SHA');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
