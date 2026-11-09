import { test, expect } from '@playwright/test';

const themes = ['light','dark','auto'] as const;
for(const width of [390,1280]){
  for(const theme of themes){
    test('brand tokens, focus and mobile viewport '+theme+' '+width,async({page})=>{
      await page.setViewportSize({width,height:844});
      await page.emulateMedia({colorScheme:theme==='auto'?'dark':'light'});
      await page.goto('/');
      await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme);
      const main=page.locator('main');
      await expect(main).toHaveClass(theme==='dark'||theme==='auto'?/dark/:/app/);
      const color=await main.evaluate(el=>{
        const cs=getComputedStyle(el);
        return {background:cs.backgroundColor,foreground:cs.color,
          action:cs.getPropertyValue('--rp-action').trim(),accent:cs.getPropertyValue('--rp-accent').trim()};
      });
      expect(color.action).toBeTruthy();
      expect(color.foreground).not.toEqual(color.background);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const nav=page.getByRole('navigation',{name:'Разделы сервиса'});
      await expect(nav).toBeVisible();
      await nav.getByRole('link',{name:'Личный кабинет'}).focus();
      await expect(nav.getByRole('link',{name:'Личный кабинет'})).toBeFocused();
      const focus=await nav.getByRole('link',{name:'Личный кабинет'}).evaluate(el=>getComputedStyle(el).outlineStyle);
      expect(focus).not.toBe('none');
      await expect(page.getByRole('link',{name:'Airmatik — главная'})).toBeVisible();
      await expect(page.getByText('Реальные заказы, оплаты и выдача автомобилей до release-gate #119 недоступны.')).toBeVisible();
      await page.screenshot({path:`test-results/brand-${theme}-${width}.png`,fullPage:true});
    });
  }
}
test('тёмная тема и клавиатура доступны на закрытом автомобильном маршруте', async ({page})=>{
 await page.goto('/');
 await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
 const link=page.getByRole('navigation',{name:'Разделы сервиса'}).getByRole('link',{name:'Личный кабинет'});
 await link.focus();
 await expect(link).toBeFocused();
 const outline=await link.evaluate(el=>getComputedStyle(el).outlineStyle);
 expect(outline).not.toBe('none');
 await link.click();
 await expect(page.getByRole('alert')).toContainText('403');
});
