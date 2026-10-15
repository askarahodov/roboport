import { test, expect } from '@playwright/test';

test('переходы по каталогу и пустые состояния через реальный API', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Сервис обслуживания роботов' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Разделы сервиса' }).getByRole('link', { name: 'Бренды' }).click();
  await expect(page).toHaveURL(/\/catalog\/brands$/);
  await expect(page.getByRole('heading', { name: 'Бренды роботов' })).toBeVisible();
  await expect(page.getByText('Пока нет опубликованных брендов.')).toBeVisible();
  await page.getByRole('navigation', { name: 'Разделы сервиса' }).getByRole('link', { name: 'Модели' }).click();
  await expect(page).toHaveURL(/\/catalog\/models$/);
  await expect(page.getByText('Пока нет опубликованных моделей.')).toBeVisible();
});

test('недоступный API показывает ошибку, не фиктивные данные', async ({ page }) => {
  await page.route('**/api/v1/catalog/brands', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ code: 'SERVICE_UNAVAILABLE' }),
  }));
  await page.goto('/catalog/brands');
  await expect(page.getByRole('alert')).toContainText('Каталог сейчас недоступен');
  await expect(page.getByText('Пока нет опубликованных брендов.')).toHaveCount(0);
});

test('переключение темы и отсутствие формы реальных заявок', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Переключить тему' }).click();
  await expect(page.locator('main')).toHaveClass(/dark|app/);
  await expect(page.getByText('Приём реальных заявок пока закрыт.')).toBeVisible();
  await expect(page.locator('form')).toHaveCount(0);
});
