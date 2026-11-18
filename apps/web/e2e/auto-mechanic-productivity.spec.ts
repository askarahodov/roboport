import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const mechanic='33333333-3333-4333-8333-333333333333';
const order='44444444-4444-4444-8444-444444444444';
const service='55555555-5555-4555-8555-555555555555';
const labor='66666666-6666-4666-8666-666666666666';
const finish='77777777-7777-4777-8777-777777777777';
const qc='88888888-8888-4888-8888-888888888888';
const closed='99999999-9999-4999-8999-999999999999';
const api='**/reports/mechanic-productivity?*';
const csv='**/reports/mechanic-productivity/export?*';

function report(fromDay:string,toDay:string){
  return {
    syntheticOnly:true,realMoney:false,payrollAvailable:false,releaseAllowed:false,
    basis:'CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES',
    periodBasis:'CLOSED_TECHNICAL_LOCAL_DAY',
    timeZone:'Europe/Berlin',fromDay,toDay,activeMechanicsOnly:true,
    limitedToEntries:500,
    summary:{mechanicCount:1,workOrderCount:1,serviceCount:1,actualMinutes:45},
    items:[{mechanicId:mechanic,workOrderCount:1,serviceCount:1,actualMinutes:45,
      orders:[{workOrderId:order,closedDay:fromDay,closedEventId:closed,finishEventId:finish,
        qcEventId:qc,serviceCount:1,actualMinutes:45,serviceIds:[service],
        laborEntries:[{laborEntryId:labor,serviceId:service,actualMinutes:45}]}]}],
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}

test('выработка: роли, проверяемые минуты, 390/1280 и тёмная тема',async({page})=>{
  await page.route(api,route=>{
    const q=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(q.get('fromDay')||'',q.get('toDay')||''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-mechanic-productivity');
  const root=page.getByLabel('Выработка механиков (dev)');
  await expect(root.getByRole('heading',{name:'Проверяемая выработка механиков'})).toBeVisible();
  await expect(root).toContainText('не зарплата, не прибыль, не выручка');
  const load=page.getByRole('button',{name:'Рассчитать выработку'});
  await expect(load).toBeDisabled();
  await scope(page);
  await expect(load).toBeEnabled();
  await load.focus();
  await page.keyboard.press('Enter');
  const data=page.getByLabel('Результаты выработки');
  await expect(data.getByRole('status')).toContainText('Факт: 45 мин.');
  await expect(data.getByRole('table',{name:'Фактические минуты с детализацией по заказ-нарядам'})
    .locator('tbody tr')).toHaveCount(1);
  await expect(data.getByRole('table')).toContainText(order);
  await data.getByText('Первичные записи труда',{exact:true}).click();
  await expect(data).toContainText(labor);
  await expect(data).toContainText(finish);
  await expect(data).toContainText(qc);
  await expect(data).toContainText(closed);
  await expect(data).toContainText('День закрытия');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:'test-results/mechanic-productivity-1280-dark.png'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'test-results/mechanic-productivity-390-dark.png'});
});

test('выработка: не принимать повреждённые суммы, 403 и лимит',async({page})=>{
  let mode:'ok'|'bad'|'denied'|'limit'='ok';
  await page.route(api,route=>{
    const q=new URL(route.request().url()).searchParams;
    const data=report(q.get('fromDay')||'',q.get('toDay')||'');
    if(mode==='bad')data.summary.actualMinutes=999;
    return route.fulfill({status:mode==='denied'?403:mode==='limit'?409:200,
      contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/staff/auto-mechanic-productivity');
  await scope(page);
  const load=page.getByRole('button',{name:'Рассчитать выработку'});
  await load.click();
  await expect(page.getByLabel('Результаты выработки')).toBeVisible();
  mode='bad';await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось сформировать отчёт');
  await expect(page.getByLabel('Результаты выработки')).toHaveCount(0);
  mode='limit';await load.click();
  await expect(page.getByRole('alert')).toContainText('более 500');
  mode='denied';await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await page.getByLabel('UUID филиала').fill('66666666-6666-4666-8666-666666666666');
  await expect(page.getByLabel('Результаты выработки')).toHaveCount(0);
});

test('выработка: CSV rechecks ACL, revoked manager clears displayed data',async({page})=>{
  let denyExport=false;
  await page.route(api,route=>{
    const q=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(q.get('fromDay')||'',q.get('toDay')||''))});
  });
  await page.route(csv,route=>route.fulfill({
    status:denyExport?403:200,
    contentType:denyExport?'application/json':'text/csv; charset=utf-8',
    body:denyExport?'{"code":"FORBIDDEN"}':'\ufeffМеханик UUID;Факт, минут\r\n'+mechanic+';45\r\n',
  }));
  await page.goto('/staff/auto-mechanic-productivity');
  await scope(page);
  await page.getByRole('button',{name:'Рассчитать выработку'}).click();
  const data=page.getByLabel('Результаты выработки');
  await expect(data).toBeVisible();
  const requested=page.waitForEvent('download');
  await data.getByRole('button',{name:'Скачать выработку CSV'}).click();
  expect((await requested).suggestedFilename()).toBe('auto-mechanic-productivity.csv');
  denyExport=true;
  await data.getByRole('button',{name:'Скачать выработку CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(data).toHaveCount(0);
});


test('выработка: reject forged labor IDs, sums and close-day source tampering',async({page})=>{
  let variant:'ok'|'duplicate'|'minutes'|'day'='ok';
  await page.route(api,route=>{
    const q=new URL(route.request().url()).searchParams;
    const result=report(q.get('fromDay')||'',q.get('toDay')||'');
    const row=result.items[0].orders[0];
    if(variant==='duplicate'){
      row.serviceCount=2;row.serviceIds.push(service);
      row.laborEntries.push({...row.laborEntries[0]});
      row.actualMinutes=90;result.items[0].actualMinutes=90;
      result.items[0].serviceCount=2;
      result.summary.actualMinutes=90;result.summary.serviceCount=2;
    }
    if(variant==='minutes')row.laborEntries[0].actualMinutes=46;
    if(variant==='day')row.closedDay='1999-01-01';
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-mechanic-productivity');await scope(page);
  const btn=page.getByRole('button',{name:'Рассчитать выработку'});
  await btn.click();
  const data=page.getByLabel('Результаты выработки');
  await expect(data).toBeVisible();
  for(const bad of ['duplicate','minutes','day'] as const){
    variant=bad;await btn.click();
    await expect(page.getByRole('alert')).toContainText('Не удалось сформировать отчёт');
    await expect(data).toHaveCount(0);
  }
  variant='ok';await btn.click();await expect(data).toBeVisible();
  await page.getByLabel('Начало периода').fill('2030-10-26');
  await page.getByLabel('Конец периода').fill('2030-10-26');
  await expect(data).toHaveCount(0);
  await btn.click();
  await expect(data).toBeVisible();
  await expect(data).toContainText('2030-10-26');
});
