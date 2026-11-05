import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const other='99999999-9999-4999-8999-999999999999';
const routePath='**/api/v1/dev/auto/organizations/**/crm/funnel?*';
const emptyReasons=()=>({PRICE:0,TIMING:0,NO_RESPONSE:0,OTHER:0});
const row=(sourceCode:string,total:number,stage:'new'|'qualified'|'booked'|'lost'|'none',
  reasons=emptyReasons())=>({
  sourceCode,total,
  new:stage==='new'?total:0,qualified:stage==='qualified'?total:0,
  booked:stage==='booked'?total:0,lost:stage==='lost'?total:0,
  bookedBasisPoints:total?(stage==='booked'?10000:0):null,
  lossReasons:reasons,
});
const full=(first:string,last:string)=>({
  syntheticOnly:true,basis:'LEAD_CREATED_COHORT_CURRENT_STAGE',
  timeZone:'Europe/Berlin',fromDay:first,toDay:last,
  summary:{
    sourceCode:'ALL',total:4,new:1,qualified:1,booked:1,lost:1,
    bookedBasisPoints:2500,
    lossReasons:{PRICE:1,TIMING:0,NO_RESPONSE:0,OTHER:0},
  },
  items:[
    {sourceCode:'WEB',total:2,new:1,qualified:0,booked:1,lost:0,
     bookedBasisPoints:5000,lossReasons:emptyReasons()},
    row('PHONE',1,'qualified'),
    row('WALK_IN',1,'lost',{PRICE:1,TIMING:0,NO_RESPONSE:0,OTHER:0}),
  ],
});
const empty=(first:string,last:string)=>({
  syntheticOnly:true,basis:'LEAD_CREATED_COHORT_CURRENT_STAGE',
  timeZone:'Europe/Berlin',fromDay:first,toDay:last,
  summary:row('ALL',0,'none'),
  items:[row('WEB',0,'none'),row('PHONE',0,'none'),row('WALK_IN',0,'none')],
});

async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}

test('CRM funnel complete manager cohort 390/1280 dark: 4 statuses, reasons, conversion',async({page})=>{
  const calls:string[]=[];
  await page.route(routePath,route=>{
    const url=new URL(route.request().url());
    const first=url.searchParams.get('fromDay')??'';
    const last=url.searchParams.get('toDay')??'';
    calls.push(url.pathname+'?'+url.searchParams.toString());
    return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify(full(first,last)),
    });
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-funnel');
  const view=page.getByLabel('Управленческая воронка CRM (dev)');
  await expect(view.getByRole('heading',{name:'Воронка обращений CRM'})).toBeVisible();
  const load=view.getByRole('button',{name:'Построить воронку CRM'});
  await expect(load).toBeDisabled();
  await scope(page);
  await expect(load).toBeEnabled();
  await load.focus();
  await page.keyboard.press('Enter');
  const results=page.getByLabel('Результаты воронки CRM');
  await expect(results.getByRole('status')).toContainText('Всего обращений: 4');
  await expect(results.getByRole('status')).toContainText('Доля записанных: 25,00 %');
  const funnel=results.getByRole('table',{name:'Текущие этапы по источникам обращений'});
  await expect(funnel.locator('tbody tr')).toHaveCount(4);
  await expect(funnel.locator('tbody tr').nth(1)).toContainText('Сайт');
  await expect(funnel.locator('tbody tr').nth(1)).toContainText('50,00 %');
  const losses=results.getByRole('table',{name:'Причины текущих отказов по источникам'});
  await expect(losses.locator('tbody tr').nth(3)).toContainText('Личное обращение');
  await expect(losses.locator('tbody tr').nth(3)).toContainText('1');
  expect(calls).toHaveLength(1);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM funnel invalid period, empty state, switching branch clears data and 403',async({page})=>{
  let denied=false;
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    const from=u.searchParams.get('fromDay')??'';
    const to=u.searchParams.get('toDay')??'';
    return route.fulfill({status:denied?403:200,contentType:'application/json',
      body:denied?'{"code":"FORBIDDEN"}':JSON.stringify(empty(from,to))});
  });
  await page.goto('/staff/auto-crm-funnel');
  await scope(page);
  const load=page.getByRole('button',{name:'Построить воронку CRM'});
  await page.getByLabel('С даты создания').fill('2030-01-01');
  await page.getByLabel('По дату создания').fill('2030-02-01');
  await expect(load).toBeDisabled();
  await page.getByLabel('По дату создания').fill('2030-01-31');
  await expect(load).toBeEnabled();
  await load.click();
  await expect(page.getByLabel('Результаты воронки CRM')).toContainText('Подтверждённых обращений за период нет');
  await expect(page.getByLabel('Результаты воронки CRM')).toContainText('Доля записанных: —');
  await page.getByLabel('UUID филиала').fill(other);
  await expect(page.getByLabel('Результаты воронки CRM')).toHaveCount(0);
  denied=true;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Результаты воронки CRM')).toHaveCount(0);
});

test('CRM funnel malformed and inconsistent reports fail closed without stale totals',async({page})=>{
  let malformed=false;
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    const from=u.searchParams.get('fromDay')??'';
    const to=u.searchParams.get('toDay')??'';
    const data=full(from,to);
    if(malformed)data.summary.total=999;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(data)});
  });
  await page.goto('/staff/auto-crm-funnel');
  await scope(page);
  const load=page.getByRole('button',{name:'Построить воронку CRM'});
  await load.click();
  await expect(page.getByLabel('Результаты воронки CRM')).toBeVisible();
  malformed=true;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить воронку CRM');
  await expect(page.getByLabel('Результаты воронки CRM')).toHaveCount(0);
  await page.getByLabel('UUID организации').fill(other);
  await expect(page.getByLabel('Результаты воронки CRM')).toHaveCount(0);
});


test('CRM funnel CSV: whole manager aggregate, fresh scope and 403 revoke',async({page})=>{
  let revoked=false;
  const exports:string[]=[];
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    const from=u.searchParams.get('fromDay')??'';
    const to=u.searchParams.get('toDay')??'';
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(full(from,to))});
  });
  await page.route('**/crm/funnel/export?*',route=>{
    const u=new URL(route.request().url());
    exports.push(u.pathname+'?'+u.searchParams.toString());
    if(revoked)return route.fulfill({status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'});
    const csv=['Источник;Всего;Новое;Обработано;Записан;Отказ;Доля записанных (б.п.)',
      'Все источники;4;1;1;1;1;2500',
      'Сайт;2;1;0;1;0;5000',
      'Звонок;1;0;1;0;0;0',
      'Личное обращение;1;0;0;0;1;0'].join('\r\n');
    return route.fulfill({status:200,contentType:'text/csv; charset=utf-8',
      headers:{'content-disposition':'attachment; filename="auto-crm-funnel.csv"'},
      body:'\ufeff'+csv});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-funnel');
  await scope(page);
  const view=page.getByLabel('Управленческая воронка CRM (dev)');
  const load=view.getByRole('button',{name:'Построить воронку CRM'});
  await expect(load).toBeEnabled();
  await load.click();
  const results=page.getByLabel('Результаты воронки CRM');
  await expect(results.getByRole('table',{
    name:'Текущие этапы по источникам обращений'}).locator('tbody tr')).toHaveCount(4);
  const downloadPromise=page.waitForEvent('download');
  await results.getByRole('button',{name:'Скачать воронку CRM в CSV'}).click();
  const file=await downloadPromise;
  expect(file.suggestedFilename()).toBe('auto-crm-funnel.csv');
  expect(exports).toHaveLength(1);
  expect(exports[0]).toContain('/locations/'+loc+'/crm/funnel/export?');
  expect(exports[0]).toContain('fromDay=');
  expect(exports[0]).toContain('toDay=');
  await expect(results.getByRole('button',{name:'Скачать воронку CRM в CSV'})).toBeEnabled();
  revoked=true;
  await results.getByRole('button',{name:'Скачать воронку CRM в CSV'}).click();
  await expect(view.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(results).toHaveCount(0);
  expect(exports).toHaveLength(2);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
});
