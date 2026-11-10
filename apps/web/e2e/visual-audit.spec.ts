import {test,expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';

// Screenshots are review evidence, NOT signed-off golden baselines.
// Real authorization is proved in PG17/HTTP tests; these fixtures are synthetic.
const sizes=[
  {name:'mobile',width:390,height:844},
  {name:'tablet',width:1280,height:900},
  {name:'desktop',width:1440,height:900},
] as const;
const themes=['light','dark','auto'] as const;
type Access='anonymous'|'unauthorized'|'forbidden'|'authorized';
type Screen={name:string;path:string;access:Access;
  staffDenied?:boolean;emptyVehicles?:boolean;expected?:string};
const screens:Screen[]=[
  {name:'landing',path:'/',access:'anonymous'},
  {name:'login',path:'/account/login',access:'anonymous'},
  {name:'customer-401',path:'/account',access:'unauthorized',
    expected:'401 — требуется локальная авторизация.'},
  {name:'customer-403',path:'/account',access:'forbidden',
    expected:'403 — доступ запрещён'},
  {name:'vehicles-empty',path:'/account/vehicles',access:'authorized',
    emptyVehicles:true,expected:'Подтверждённых автомобилей пока нет.'},
  {name:'customer-orders-denied',path:'/account/auto-orders',access:'authorized'},
  {name:'customer-booking-denied',path:'/account/auto-booking',access:'authorized'},
  {name:'staff-403',path:'/staff',access:'anonymous',staffDenied:true},
  {name:'staff-dashboard',path:'/staff',access:'anonymous'},
  {name:'staff-calendar',path:'/staff/auto-calendar',access:'anonymous'},
  {name:'staff-intake',path:'/staff/auto-intake-act',access:'anonymous'},
  {name:'staff-diagnostics',path:'/staff/auto-diagnostics',access:'anonymous'},
  {name:'staff-estimates',path:'/staff/auto-estimates',access:'anonymous'},
  {name:'staff-qc',path:'/staff/auto-workshop-qc',access:'anonymous'},
  {name:'staff-execution',path:'/staff/auto-execution',access:'anonymous'},
  {name:'supplier-orders',path:'/staff/warehouse-supplier-orders',access:'anonymous'},
  {name:'warehouse-receiving',path:'/staff/warehouse-receiving',access:'anonymous'},
  {name:'crm-leads',path:'/staff/auto-crm-leads',access:'anonymous'},
  {name:'finance-preview',path:'/staff/auto-demo-close',access:'anonymous'},
  {name:'ui-gallery',path:'/dev/ui-gallery',access:'anonymous'},
  {name:'unknown-404',path:'/not-a-current-route',access:'anonymous'},
];
const json=(data:unknown,status=200)=>({status,contentType:'application/json',
  headers:{'Cache-Control':'no-store'},body:JSON.stringify(data)});

for(const size of sizes){
  for(const theme of themes){
    test('auto visual evidence '+size.name+'/'+theme,async({page},testInfo)=>{
      test.setTimeout(240_000);
      const pageErrors:string[]=[];
      page.on('pageerror',error=>pageErrors.push(error.message));
      await page.setViewportSize({width:size.width,height:size.height});
      await page.emulateMedia({colorScheme:theme==='auto'?'dark':'light',
        reducedMotion:'reduce'});
      let current:Screen=screens[0];
      await page.route('**/api/v1/auth/me',route=>route.fulfill(
        current.access==='authorized'
          ?json({subjectId:'11111111-1111-4111-8111-111111111111'})
          :json({code:current.access==='forbidden'?'FORBIDDEN':'UNAUTHENTICATED'},
            current.access==='forbidden'?403:401)));
      await page.route('**/api/v1/dev/staff/access',route=>route.fulfill(
        current.staffDenied?json({code:'FORBIDDEN'},403)
          :json({roles:['INTAKE','ENGINEER','STOCK','MANAGER','FINANCE']})));
      await page.route('**/api/v1/dev/auto/**',route=>{
        if(current.emptyVehicles &&
          new URL(route.request().url()).pathname==='/api/v1/dev/auto/vehicles')
          return route.fulfill(json({items:[]}));
        return route.fulfill(json({code:'FORBIDDEN'},403));
      });
      const folder=testInfo.outputPath('visual');
      await mkdir(folder,{recursive:true});
      const report:{screen:string;path:string;state:string;png:string}[]=[];
      for(const screen of screens){
        current=screen;
        await page.goto(screen.path,{waitUntil:'domcontentloaded'});
        await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme);
        await expect(page.locator('main')).toHaveCount(1);
        await expect(page.locator('main h1').first()).toBeVisible();
        if(screen.expected)
          await expect(page.getByText(screen.expected,{exact:false}).first()).toBeVisible();
        if(screen.staffDenied)
          await expect(page.getByRole('alert').first()).toBeVisible();
        await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth),
          {message:'root overflow at '+screen.name+'/'+size.width})
          .toBeLessThanOrEqual(size.width+1);
        const filename=screen.name+'-'+size.name+'-'+theme+'.png';
        await page.screenshot({path:testInfo.outputPath('visual',filename),
          fullPage:true,animations:'disabled',caret:'hide'});
        report.push({screen:screen.name,path:screen.path,
          state:screen.emptyVehicles?'empty':screen.staffDenied?'403':screen.access,
          png:filename});
      }
      await writeFile(testInfo.outputPath('visual','manifest.json'),
        JSON.stringify({syntheticOnly:true,approvedBaseline:false,
          viewport:size.width,theme,scenarios:report},null,2));
      expect(pageErrors,'Browser JS errors in visual matrix').toEqual([]);
    });
  }
}

test('synthetic UI gallery: keyboard, dialog, disabled and textual status',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/dev/ui-gallery');
  await expect(page.getByRole('heading',
    {name:'БоксРитм — проверка компонентов (dev)'})).toBeVisible();
  const open=page.getByRole('button',{name:'Открыть диалог'});
  await open.focus();
  await expect(open).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog',{name:'Пример диалога'})).toBeVisible();
  await page.getByRole('button',{name:'Закрыть диалог'}).click();
  await expect(page.getByRole('dialog',{name:'Пример диалога'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Недоступное действие'})).toBeDisabled();
  await expect(page.getByText('QC FAIL')).toBeVisible();
  await expect(page.getByText('403 — нет доступа.',{exact:false})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
