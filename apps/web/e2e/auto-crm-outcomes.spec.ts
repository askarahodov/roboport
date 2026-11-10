import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const route='**/api/v1/dev/auto/organizations/**/crm/outcomes?*';
const empty=(sourceCode:string)=>({
  sourceCode,bookedLeads:0,closedTechnical:0,
  simSettledMinor:'0',simRefundMinor:'0',
});
function result(fromDay:string,toDay:string){
  return {
    syntheticOnly:true,realMoney:false,releaseAllowed:false,
    revenueAvailable:false,basis:'LEAD_COHORT_CLOSED_TECHNICAL_SIM_TREASURY',
    currency:'RUB',timeZone:'Europe/Berlin',fromDay,toDay,
    summary:{sourceCode:'ALL',bookedLeads:2,closedTechnical:1,
      simSettledMinor:'2500',simRefundMinor:'300'},
    items:[
      {sourceCode:'WEB',bookedLeads:2,closedTechnical:1,
        simSettledMinor:'2500',simRefundMinor:'300'},
      empty('PHONE'),empty('WALK_IN'),
    ],
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}
test('CRM completed orders and SIM sums: responsive and explicit no-revenue caveat',async({page})=>{
  const requests:string[]=[];
  await page.route(route,request=>{
    const u=new URL(request.request().url());
    requests.push(u.pathname);
    return request.fulfill({status:200,contentType:'application/json',body:JSON.stringify(
      result(u.searchParams.get('fromDay')||'',u.searchParams.get('toDay')||''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-outcomes');
  const view=page.getByLabel('Техническая конверсия CRM (dev)');
  await expect(view.getByRole('heading',{name:'CRM: запись → закрытие → тестовые расчёты'}))
    .toBeVisible();
  await expect(view).toContainText('не выручка');
  const load=view.getByRole('button',{name:'Проверить техническую конверсию'});
  await expect(load).toBeDisabled();
  await scope(page);
  await load.focus();
  await page.keyboard.press('Enter');
  const report=page.getByLabel('Результаты технической конверсии');
  await expect(report.getByRole('status')).toContainText('Технически закрыто: 1');
  await expect(report.getByRole('status')).toContainText('25,00 ₽');
  const table=report.getByRole('table',{
    name:'Синтетическая конверсия по источнику лида'});
  await expect(table.locator('tbody tr')).toHaveCount(4);
  await expect(table.locator('tbody tr').nth(1)).toContainText('3,00 ₽');
  expect(requests).toHaveLength(1);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM outcomes deny on revoked scope, malformed totals and stale data',async({page})=>{
  let mode:'ok'|'bad'|'denied'='ok';
  await page.route(route,request=>{
    const u=new URL(request.request().url());
    const payload=result(u.searchParams.get('fromDay')||'',u.searchParams.get('toDay')||'');
    if(mode==='bad')payload.summary.simSettledMinor='99999999';
    return request.fulfill({status:mode==='denied'?403:200,
      contentType:'application/json',
      body:mode==='denied'?'{"code":"FORBIDDEN"}':JSON.stringify(payload)});
  });
  await page.goto('/staff/auto-crm-outcomes');
  await scope(page);
  const load=page.getByRole('button',{name:'Проверить техническую конверсию'});
  await load.click();
  await expect(page.getByLabel('Результаты технической конверсии')).toBeVisible();
  mode='bad';
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось построить отчёт');
  await expect(page.getByLabel('Результаты технической конверсии')).toHaveCount(0);
  mode='denied';
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await page.getByLabel('UUID филиала').fill('99999999-9999-4999-8999-999999999999');
  await expect(page.getByLabel('Результаты технической конверсии')).toHaveCount(0);
});

test('CRM outcomes CSV export rechecks grants and clears report on 403',async({page})=>{
  let denied=false;
  await page.route(route,request=>{
    const u=new URL(request.request().url());
    return request.fulfill({status:200,contentType:'application/json',body:JSON.stringify(
      result(u.searchParams.get('fromDay')||'',u.searchParams.get('toDay')||''))});
  });
  await page.route('**/crm/outcomes/export?*',request=>
    request.fulfill({status:denied?403:200,
      contentType:denied?'application/json':'text/csv; charset=utf-8',
      headers:{'content-disposition':'attachment; filename="auto-crm-outcomes.csv"'},
      body:denied?'{"code":"FORBIDDEN"}':'\ufeffИсточник;Записанных лидов\r\nСайт;2\r\n'}));
  await page.goto('/staff/auto-crm-outcomes');
  await scope(page);
  await page.getByRole('button',{name:'Проверить техническую конверсию'}).click();
  const report=page.getByLabel('Результаты технической конверсии');
  await expect(report).toBeVisible();
  const download=page.waitForEvent('download');
  await report.getByRole('button',{name:'Скачать техническую сверку CSV'}).click();
  expect((await download).suggestedFilename()).toBe('auto-crm-outcomes.csv');
  denied=true;
  await report.getByRole('button',{name:'Скачать техническую сверку CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(report).toHaveCount(0);
});
