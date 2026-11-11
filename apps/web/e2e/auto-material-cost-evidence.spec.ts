import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const workOrder='33333333-3333-4333-8333-333333333333';
const lot='44444444-4444-4444-8444-444444444444';
const sku='55555555-5555-4555-8555-555555555555';
const issue='66666666-6666-4666-8666-666666666666';
const returned='77777777-7777-4777-8777-777777777777';
const reportRoute='**/reports/material-cost-evidence?*';
const csvRoute='**/reports/material-cost-evidence/export?*';
function payload(fromDay:string,toDay:string){
  return {
    syntheticOnly:true,realMoney:false,marginAvailable:false,releaseAllowed:false,
    priceIsOnlyReceiptQuote:true,basis:'IMMUTABLE_LOT_MOVEMENTS_RECEIPT_QUOTE_PROVENANCE',
    timeZone:'Europe/Berlin',fromDay,toDay,
    summary:{workOrderCount:1,lotCount:1,movementCount:2,
      grossIssued:3,grossReturned:1,netPeriod:2,evidenceCounts:{
        NO_RECEIPT:1,REVIEW_REQUIRED:0,SINGLE_VALUED_RECEIPT:0}},
    items:[{workOrderId:workOrder,lotId:lot,skuId:sku,
      grossIssued:3,grossReturned:1,netPeriod:2,receiptCount:0,
      acceptedReceiptCount:0,valuedReceiptCount:0,quoteEvidence:'NO_RECEIPT',
      quoteValuationId:null,quoteCurrency:null,quoteUnitNetMinor:null,
      movementCount:2,movementIds:[issue,returned]}],
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}

test('первичка материалов: 390/1280, keyboard, dark and explicit not COGS',async({page})=>{
  await page.route(reportRoute,route=>{
    const q=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(q.get('fromDay')||'',q.get('toDay')||''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-material-cost-evidence');
  const view=page.getByLabel('Аудит закупочной первички (dev)');
  await expect(view.getByRole('heading',{name:'Проверка происхождения стоимости материалов'}))
    .toBeVisible();
  await expect(view).toContainText('не доказывает себестоимость');
  const load=view.getByRole('button',{name:'Проверить документы материалов'});
  await expect(load).toBeDisabled();
  await scope(page);
  await load.focus();await page.keyboard.press('Enter');
  const report=page.getByLabel('Результаты аудита материалов');
  await expect(report.getByRole('status')).toContainText('Выдано: 3');
  await expect(report).toContainText('Без приходов: 1');
  await expect(report.getByRole('table').locator('tbody tr')).toHaveCount(1);
  await expect(report.getByRole('table')).toContainText(workOrder);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.setViewportSize({width:1280,height:800});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:'test-results/material-cost-evidence-1280-dark.png'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'test-results/material-cost-evidence-390-dark.png'});
});

test('первичка материалов: deny, corrupt totals, 409 and stale scope',async({page})=>{
  let mode:'ok'|'bad'|'denied'|'limit'='ok';
  await page.route(reportRoute,route=>{
    const q=new URL(route.request().url()).searchParams;
    const report=payload(q.get('fromDay')||'',q.get('toDay')||'');
    if(mode==='bad')report.summary.grossIssued=999;
    return route.fulfill({status:mode==='denied'?403:mode==='limit'?409:200,
      contentType:'application/json',body:JSON.stringify(report)});
  });
  await page.goto('/staff/auto-material-cost-evidence');
  await scope(page);
  const load=page.getByRole('button',{name:'Проверить документы материалов'});
  await load.click();
  await expect(page.getByLabel('Результаты аудита материалов')).toBeVisible();
  mode='bad';await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось сформировать аудит');
  await expect(page.getByLabel('Результаты аудита материалов')).toHaveCount(0);
  mode='limit';await load.click();
  await expect(page.getByRole('alert')).toContainText('Более 500 событий');
  mode='denied';await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await page.getByLabel('UUID филиала').fill('88888888-8888-4888-8888-888888888888');
  await expect(page.getByLabel('Результаты аудита материалов')).toHaveCount(0);
});

test('первичка материалов: download rechecks permissions',async({page})=>{
  let denied=false;
  await page.route(reportRoute,route=>{
    const q=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(q.get('fromDay')||'',q.get('toDay')||''))});
  });
  await page.route(csvRoute,route=>route.fulfill({
    status:denied?403:200,
    contentType:denied?'application/json':'text/csv; charset=utf-8',
    body:denied?'{"code":"FORBIDDEN"}':'\ufeffЗаказ-наряд UUID;Выдано\r\n'+workOrder+';3\r\n',
  }));
  await page.goto('/staff/auto-material-cost-evidence');
  await scope(page);
  await page.getByRole('button',{name:'Проверить документы материалов'}).click();
  const report=page.getByLabel('Результаты аудита материалов');
  await expect(report).toBeVisible();
  const download=page.waitForEvent('download');
  await report.getByRole('button',{name:'Скачать аудит CSV'}).click();
  expect((await download).suggestedFilename()).toBe('auto-material-cost-evidence.csv');
  denied=true;
  await report.getByRole('button',{name:'Скачать аудит CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(report).toHaveCount(0);
});
