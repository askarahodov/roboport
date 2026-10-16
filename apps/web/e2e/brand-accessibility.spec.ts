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
      expect(color.action).not.toEqual(color.accent);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const nav=page.getByRole('navigation',{name:'Разделы сервиса'});
      await expect(nav).toBeVisible();
      await nav.getByRole('link',{name:'Бренды'}).focus();
      await expect(nav.getByRole('link',{name:'Бренды'})).toBeFocused();
      const focus=await nav.getByRole('link',{name:'Бренды'}).evaluate(el=>getComputedStyle(el).outlineStyle);
      expect(focus).not.toBe('none');
      const logo=page.getByRole('link',{name:'ROBOport — главная'}).locator('img');
      await expect(logo).toHaveAttribute('src',/logo-light\.svg/);
      await expect(page.getByText('Приём реальных заявок пока закрыт.')).toBeVisible();
      await page.screenshot({path:`test-results/brand-${theme}-${width}.png`,fullPage:true});
    });
  }
}
test('alert contrast tokens remain active in dark mode and error is keyboard accessible',async({page})=>{
 await page.route('**/api/v1/catalog/brands?*',r=>r.fulfill({status:503,body:'{}'}));
 await page.goto('/catalog/brands');
 await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
 const alert=page.getByRole('alert');
 await expect(alert).toBeVisible();
 const styles=await alert.evaluate(el=>{const s=getComputedStyle(el);return {color:s.color,background:s.backgroundColor};});
 expect(styles.color).not.toBe(styles.background);
 await page.getByRole('button',{name:'Повторить запрос'}).focus();
 await expect(page.getByRole('button',{name:'Повторить запрос'})).toBeFocused();
});
