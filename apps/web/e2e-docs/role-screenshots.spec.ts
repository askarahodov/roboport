import {test,expect} from '@playwright/test';

// These are actual Docsify pages backed by the repository's Markdown, NOT
// mockups or screenshots of customers. CI publishes the PNGs with the SHA.
const roles=[
  {id:'administrator',heading:'Администратор'},
  {id:'intake',heading:'Мастер-приёмщик'},
  {id:'mechanic',heading:'Механик и диагност'},
  {id:'warehouse',heading:'Кладовщик и закупщик'},
  {id:'manager',heading:'Руководитель'},
  {id:'finance',heading:'Бухгалтер и кассир'},
  {id:'client',heading:'Клиент автосервиса'},
  {id:'mobile',heading:'Мобильное приложение'},
] as const;

for(const role of roles){
  test('безопасные скриншоты справки: '+role.id+' × 390/1280 × light/dark',async({page},info)=>{
    test.setTimeout(120_000);
    for(const width of [390,1280]){
      await page.setViewportSize({width,height:844});
      for(const colorScheme of ['light','dark'] as const){
        await page.emulateMedia({colorScheme});
        await page.goto('/docs/#/guides/'+role.id);
        await expect(page.locator('.markdown-section h1')).toContainText(
          role.heading,{timeout:25_000});
        await expect(page.locator('.markdown-section')).not.toContainText('404 - Not found');
        await expect(page.locator('.markdown-section')).toContainText(
          /synthetic|dev|planned|планируется|Статус/i);
        const viewport=await page.evaluate(()=>({
          width:document.documentElement.scrollWidth,
          max:document.documentElement.clientWidth,
        }));
        expect(viewport.width,'Горизонтальное переполнение: '+role.id).toBeLessThanOrEqual(
          Math.max(width,viewport.max)+2);
        // Viewport captures keep long warehouse guides readable and the
        // artifact bounded; the complete content stays in Markdown.
        await page.screenshot({
          path:info.outputPath('guide-'+role.id+'-'+width+'-'+colorScheme+'.png'),
          animations:'disabled',
        });
      }
    }
  });
}

test('скриншоты мобильной навигации и статусов current/planned',async({page},info)=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/docs/');
  await expect(page.locator('.markdown-section h1')).toContainText(
    'База знаний Airmatik Pro Service',{timeout:25_000});
  await page.locator('.sidebar-toggle').click();
  await expect(page.locator('.sidebar-nav a[href*="guides/QUICK_START"]')).toBeVisible();
  await expect(page.locator('.sidebar-nav a[href*="guides/FAQ"]')).toBeVisible();
  await page.screenshot({
    path:info.outputPath('docs-mobile-menu-current-planned.png'),
    animations:'disabled',
  });
  const search=page.locator('.sidebar .search input');
  await search.fill('смета');
  await expect(page.locator('.sidebar .results-panel a').first()).toBeVisible();
  await page.screenshot({
    path:info.outputPath('docs-mobile-search-synthetic.png'),
    animations:'disabled',
  });
});
