import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';

const sizes=[{name:'desktop',width:1440,height:900},{name:'tablet',width:1280,height:900},{name:'mobile',width:390,height:844}];
const themes=['light','dark','auto'] as const;
const screens=[
 {name:'landing',url:'/'},
 {name:'automotive-landing',url:'/'},
 {name:'login',url:'/account/login'},
 {name:'customer-401',url:'/account'},
 {name:'staff',url:'/staff'},
] as const;

for(const size of sizes){
 for(const theme of themes){
  test('visual review evidence '+size.name+'/'+theme,async({page})=>{
   await page.setViewportSize({width:size.width,height:size.height});
   await page.route('**/api/v1/auth/me',route=>route.fulfill({status:401,contentType:'application/json',body:'{"code":"UNAUTHENTICATED"}'}));
   await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({status:200,contentType:'application/json',body:'{"roles":["ENGINEER"]}'}));
   await page.route('**/api/v1/dev/staff/queue?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[
    {id:'11111111-1111-4111-8111-111111111111',state:'DIAGNOSING',version:3},
    {id:'22222222-2222-4222-8222-222222222222',state:'SAFETY_HOLD',version:4}
   ]})}));
   for(const screen of screens){
    await page.goto(screen.url);
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme);
    await expect(page.locator('main')).toHaveCount(1);
    await expect(page.locator('h1').first()).toBeVisible();
    await expect.poll(async()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width);
    const filename='test-results/visual/'+screen.name+'-'+size.name+'-'+theme+'.png';
    await mkdir('test-results/visual',{recursive:true});
    await page.screenshot({path:filename,fullPage:true,animations:'disabled'});
   }
  });
 }
}

test('автомобильный staff portal доступен с клавиатуры при 390px',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({status:200,contentType:'application/json',body:'{"roles":["ENGINEER"]}'}));
 await page.goto('/staff');
 await expect(page.getByRole('heading',{name:'Рабочее место СТО'})).toBeVisible();
 const nav=page.getByRole('navigation',{name:'Навигация СТО'});
 const link=nav.getByRole('link',{name:'Диагностика автомобиля (dev)'});
 await link.focus();
 await expect(link).toBeFocused();
 await expect(page.getByRole('button',{name:'Выдать устройство'})).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
