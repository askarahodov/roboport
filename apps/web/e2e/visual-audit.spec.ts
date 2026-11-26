import {test,expect,type Page,type TestInfo} from '@playwright/test';
import {mkdir,writeFile,readFile,stat} from 'node:fs/promises';

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

type VisualResult={
  screen:string;path:string;state:string;png:string;
  captured:boolean;checks:'PASS'|'FAIL';failureKinds:string[];
};

// Shared collector: the main audit MUST fail when this reports a failure.
// The negative regression deliberately creates a broken page to prove that
// evidence for later pages is still written to disk.
async function collectVisualEvidence(
  page:Page,testInfo:TestInfo,selectedScreens:readonly Screen[],
  sizeName:string,width:number,theme:typeof themes[number],
  setCurrent:(screen:Screen)=>void,
  afterNavigation?:(page:Page,screen:Screen)=>Promise<void>,
){
  let pageErrorCount=0;
  const trackError=()=>{pageErrorCount++;};
  page.on('pageerror',trackError);
  try{
    const folder=testInfo.outputPath('visual');
        await mkdir(folder,{recursive:true});
        // Fail after the complete matrix, not at the first broken route.
        // This keeps review evidence for subsequent screens and error states.
        const report:VisualResult[]=[];
        const failures:string[]=[];
        for(const screen of selectedScreens){
          setCurrent(screen);
          const failureKinds:string[]=[];
          const errorOffset=pageErrorCount;
          let check='navigation';
          const filename=screen.name+'-'+sizeName+'-'+theme+'.png';
          try{
            await page.goto(screen.path,{waitUntil:'domcontentloaded',timeout:6000});
            if(afterNavigation)await afterNavigation(page,screen);
            check='theme';
            await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme,{timeout:2500});
            check='main';
            await expect(page.locator('main')).toHaveCount(1,{timeout:3000});
            check='heading';
            await expect(page.locator('main h1').first()).toBeVisible({timeout:3000});
            check='expected-state';
            if(screen.expected)
              await expect(page.getByText(screen.expected,{exact:false}).first()).toBeVisible({timeout:3000});
            check='access-alert';
            if(screen.staffDenied)
              await expect(page.getByRole('alert').first()).toBeVisible({timeout:3000});
            check='controls';
            // Используем вычисленное accessible name Playwright, а не наличие
            // атрибута: ссылка на несуществующий ID и скрытый текст не дают имени.
            // Placeholder отдельно исключён как замена подписи поля.
            const controls=await page.locator(
              'main button,main a[href],main input:not([type=hidden]),'+
              'main select,main textarea,main [role=button],main [role=link]'
            ).filter({visible:true}).all();
            const labels=page.getByLabel(/\S/);
            const unnamed=await Promise.all(controls.map(async control=>{
              const needsLabel=await control.evaluate(el=>
                el.matches('input[placeholder],textarea[placeholder]') &&
                !el.getAttribute('title')?.trim());
              if(needsLabel && await control.and(labels).count()===0)return true;
              try{
                await expect(control).toHaveAccessibleName(/\S/,{timeout:750});
                return false;
              }catch{
                // В manifest сохраняется только категория, не текст имени.
                return true;
              }
            }));
            if(unnamed.some(Boolean))failureKinds.push('A11Y_UNNAMED_CONTROLS');
            check='overflow';
            await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth),
              {message:'root overflow at '+screen.name+'/'+width,timeout:3000})
              .toBeLessThanOrEqual(width+1);
            check='path';
            if(new URL(page.url()).pathname!==screen.path)
              failureKinds.push('NAVIGATION_MISMATCH');
          }catch{
            // Only the stage is persisted, not assertion text, URLs or response bodies.
            failureKinds.push('RENDER_'+check.toUpperCase());
          }
          let captured=false;
          try{
            // Even when a render assertion fails, its screenshot is useful for review.
            await page.screenshot({path:testInfo.outputPath('visual',filename),
              fullPage:true,animations:'disabled',caret:'hide',timeout:10000});
            captured=true;
          }catch(error){
            failureKinds.push('CAPTURE_'+(error instanceof Error?error.name:'UnknownError'));
          }
          if(pageErrorCount>errorOffset)
            failureKinds.push('BROWSER_PAGEERROR');
          report.push({screen:screen.name,path:screen.path,
            state:screen.emptyVehicles?'empty':screen.staffDenied?'403':screen.access,
            png:filename,captured,
            checks:failureKinds.length?'FAIL':'PASS',failureKinds});
          if(failureKinds.length)
            failures.push(screen.name+': '+failureKinds.join(', '));
        }
        await writeFile(testInfo.outputPath('visual','manifest.json'),
          JSON.stringify({syntheticOnly:true,approvedBaseline:false,
            viewport:width,theme,scenarios:report},null,2));
    return {report,failures,pageErrorCount};
  }finally{
    page.off('pageerror',trackError);
  }
}

for(const size of sizes){
  for(const theme of themes){
    test('auto visual evidence '+size.name+'/'+theme,async({page},testInfo)=>{
      // 21 screens with bounded per-screen failures still need a whole-matrix budget.
      test.setTimeout(720_000);
      await page.setViewportSize({width:size.width,height:size.height});
      await page.emulateMedia({colorScheme:theme==='light'?'light':'dark',
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
          return route.fulfill(json({items:[],nextCursor:null}));
        return route.fulfill(json({code:'FORBIDDEN'},403));
      });
      const {report,failures,pageErrorCount}=await collectVisualEvidence(
        page,testInfo,screens,size.name,size.width,theme,screen=>{current=screen;});
      expect(report).toHaveLength(screens.length);
      expect(pageErrorCount,'Unexpected browser page errors').toBe(0);
      expect(failures,'Visual failures (all route evidence was attempted)').toEqual([]);

    });
  }
}

test('synthetic UI gallery: keyboard focus indicator is visible',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/dev/ui-gallery');
  const open=page.getByRole('button',{name:'Открыть диалог'});
  // Starting from an actual keyboard event triggers :focus-visible semantics.
  await page.keyboard.press('Tab');
  await open.focus();
  const indicator=await open.evaluate(el=>{
    const css=getComputedStyle(el);
    return {width:parseFloat(css.outlineWidth),style:css.outlineStyle,
      offset:parseFloat(css.outlineOffset)};
  });
  expect(indicator.style).not.toBe('none');
  expect(indicator.width).toBeGreaterThanOrEqual(2);
  expect(indicator.offset).toBeGreaterThanOrEqual(0);
});

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

test('visual evidence continues after a deliberately broken screen',async({page},testInfo)=>{
  test.setTimeout(90_000);
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
  // Isolated synthetic route, not a real application defect or production API.
  await page.route('**/__visual-evidence-broken',route=>route.fulfill({
    status:200,contentType:'text/html',
    body:'<!doctype html><html lang="ru"><body><main><h1>Сломанный тестовый экран</h1></main></body></html>',
  }));
  const sample:Screen[]=[
    {name:'before-failure',path:'/',access:'anonymous'},
    {name:'intentional-failure',path:'/__visual-evidence-broken',access:'anonymous'},
    {name:'after-failure',path:'/account/login',access:'anonymous'},
  ];
  const {report,failures,pageErrorCount}=await collectVisualEvidence(
    page,testInfo,sample,'mobile',390,'light',()=>{});
  expect(pageErrorCount).toBe(0);
  expect(report.map(row=>row.screen)).toEqual(sample.map(row=>row.name));
  expect(report.map(row=>row.checks)).toEqual(['PASS','FAIL','PASS']);
  expect(report[1].failureKinds).toContain('RENDER_THEME');
  expect(failures).toEqual(['intentional-failure: RENDER_THEME']);
  expect(report.every(row=>row.captured)).toBe(true);
  for(const row of report){
    expect((await stat(testInfo.outputPath('visual',row.png))).size).toBeGreaterThan(0);
  }
  const manifest=JSON.parse(await readFile(testInfo.outputPath('visual','manifest.json'),'utf8'));
  expect(manifest).toMatchObject({
    syntheticOnly:true,approvedBaseline:false,viewport:390,theme:'light',
  });
  expect(manifest.scenarios).toEqual(report);
  expect(JSON.stringify(manifest)).not.toMatch(/password|token|cookie/i);
});

test('accessibility evidence rejects unnamed controls and accepts real accessible names',
  async({page},testInfo)=>{
    test.setTimeout(120_000);
    await page.setViewportSize({width:390,height:844});
    const variants=[
      {name:'unnamed-button',control:'<button type="button"></button>'},
      {name:'unnamed-select',control:'<select><option>Ожидание</option></select>'},
      {name:'unnamed-textarea',control:'<textarea>Начальный текст</textarea>'},
      {name:'dangling-label',control:'<button type="button" aria-labelledby="absent-visual-label"></button>'},
      {name:'blank-label',control:'<span id="empty-visual-label"> </span><button type="button" aria-labelledby="empty-visual-label"></button>'},
      {name:'hidden-text',control:'<button type="button"><span aria-hidden="true">Скрытая подпись</span></button>'},
      {name:'placeholder-only',control:'<input placeholder="Не подпись">'},
      {name:'placeholder-dangling-label',control:'<input placeholder="Не подпись" aria-labelledby="absent-visual-label">'},
      {name:'valid-labelledby',control:'<span id="valid-visual-label">Действие</span><button type="button" aria-labelledby="valid-visual-label"></button>',pass:true},
      {name:'valid-input',control:'<label for="visual-labelled-input">Название</label><input id="visual-labelled-input" placeholder="Подсказка">',pass:true},
      {name:'valid-aria-label',control:'<button type="button" aria-label="Действие"><span aria-hidden="true">Иконка</span></button>',pass:true},
    ] as const;
    const screens:Screen[]=variants.map(v=>({
      name:v.name,path:'/dev/ui-gallery',access:'anonymous',
    }));
    const {report,failures}=await collectVisualEvidence(
      page,testInfo,screens,'mobile',390,'light',()=>{},
      async(p,screen)=>{
        const fixture=variants.find(v=>v.name===screen.name);
        if(!fixture)throw Error('unknown synthetic a11y fixture');
        await p.locator('main').evaluate((main,html)=>{
          main.insertAdjacentHTML('beforeend',html);
        },fixture.control);
      });
    expect(report).toHaveLength(variants.length);
    expect(report.map(row=>row.screen)).toEqual(variants.map(v=>v.name));
    for(const [index,row] of report.entries()){
      const passed='pass' in variants[index];
      expect(row).toMatchObject({
        checks:passed?'PASS':'FAIL',captured:true,
        failureKinds:passed?[]:['A11Y_UNNAMED_CONTROLS'],
      });
      expect((await stat(testInfo.outputPath('visual',row.png))).size)
        .toBeGreaterThan(0);
    }
    expect(failures).toEqual(variants.filter(v=>!('pass' in v))
      .map(v=>v.name+': A11Y_UNNAMED_CONTROLS'));
    const manifest=JSON.parse(await readFile(testInfo.outputPath('visual','manifest.json'),'utf8'));
    expect(manifest.scenarios).toEqual(report);
  });
