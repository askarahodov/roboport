import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const wrong='99999999-9999-4999-8999-999999999999';
const routePath='**/api/v1/dev/auto/organizations/**/stock-abc-xyz?*';
const monthLabel=(n:number)=>{
  const year=Math.floor(n/12),month=n%12+1;
  return year.toString().padStart(4,'0')+'-'+month.toString().padStart(2,'0');
};
const completedMonths=()=>{
  const today=new Date();
  const current=today.getUTCFullYear()*12+today.getUTCMonth();
  return [monthLabel(current-3),monthLabel(current-2),monthLabel(current-1)];
};
const id=(i:number)=>'aaaaaaaa-aaaa-4aaa-8aaa-'+String(i+1).padStart(12,'0');
const profiles=[
  {article:'SYN-A',grossIssued:180,grossReturned:0,
   monthlyIssued:[60,60,60],abc:'A',xyz:'X',variationBasisPoints:0},
  {article:'SYN-B',grossIssued:75,grossReturned:0,
   monthlyIssued:[25,25,25],abc:'A',xyz:'X',variationBasisPoints:0},
  {article:'SYN-C',grossIssued:45,grossReturned:5,
   monthlyIssued:[12,15,18],abc:'B',xyz:'Y',variationBasisPoints:1632},
  {article:'SYN-D',grossIssued:15,grossReturned:0,
   monthlyIssued:[0,0,15],abc:'C',xyz:'Z',variationBasisPoints:14142},
];
const items=Array.from({length:29},(_,i)=>{
  const row=i<4?profiles[i]:{
    article:'SYN-'+i,grossIssued:0,grossReturned:0,
    monthlyIssued:[0,0,0],abc:null,xyz:null,variationBasisPoints:null,
  };
  return {...row,skuId:id(i),
    netPeriod:row.grossIssued-row.grossReturned};
});
function body(from:string,to:string,offset:number){
  return {
    syntheticOnly:true,basis:'GROSS_ISSUE_UNITS_CLOSED_MONTHS',
    moneyValuation:false,timeZone:'Europe/Berlin',
    fromMonth:from,toMonth:to,months:completedMonths(),
    pageSize:25,offset,nextOffset:offset===0?25:null,
    summary:{
      skuCount:29,activeSkus:4,grossIssued:315,
      grossReturned:5,netPeriod:310,
      classCounts:{A:2,B:1,C:1,X:2,Y:1,Z:1,none:25},
    },
    items:items.slice(offset,offset+25),
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}
test('manager complete ABC/XYZ: 29 SKU pages, returns, 390/1280/dark and keyboard',async({page})=>{
  const queries:number[]=[];
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    const offset=Number(u.searchParams.get('offset')??0);
    queries.push(offset);
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(body(
        u.searchParams.get('fromMonth')??'',
        u.searchParams.get('toMonth')??'',offset,
      ))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-stock-abc-xyz');
  const panel=page.getByLabel('Операционный ABC XYZ склада (dev)');
  const load=panel.getByRole('button',{name:'Построить ABC/XYZ'});
  await expect(load).toBeDisabled();
  await scope(page);
  const months=completedMonths();
  await panel.getByLabel('С месяца').fill(months[0]);
  await panel.getByLabel('По месяц').fill(months[2]);
  await expect(panel.getByLabel('С месяца')).toHaveValue(months[0]);
  await expect(panel.getByLabel('По месяц')).toHaveValue(months[2]);
  await load.focus();
  await page.keyboard.press('Enter');
  const results=page.getByLabel('Результаты ABC XYZ');
  await expect(results).toContainText('Всего SKU: 29');
  await expect(results).toContainText('Выдано: 315');
  await expect(results).toContainText('возвращено: 5');
  await expect(results.getByRole('status')).toContainText('ABC: A 2, B 1, C 1');
  const table=results.getByRole('table',{
    name:'Все позиции склада по количеству выдач, 25 на страницу',
  });
  await expect(table.locator('tbody tr')).toHaveCount(25);
  await expect(table.locator('tbody tr').first()).toContainText('SYN-A');
  await expect(table.locator('tbody tr').nth(2)).toContainText('SYN-C');
  await expect(table.locator('tbody tr').nth(2)).toContainText('40');
  const nav=results.getByLabel('Страницы ABC XYZ');
  await expect(nav.getByRole('button',{name:'Предыдущая страница'})).toBeDisabled();
  await nav.getByRole('button',{name:'Следующая страница'}).click();
  await expect(table.locator('tbody tr')).toHaveCount(4);
  await expect(nav).toContainText('Страница 2');
  await expect(nav.getByRole('button',{name:'Следующая страница'})).toBeDisabled();
  await nav.getByRole('button',{name:'Предыдущая страница'}).click();
  await expect(table.locator('tbody tr')).toHaveCount(25);
  expect(queries).toEqual([0,25,0]);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('manager ABC/XYZ invalid/403/409 clears stale data and cannot leak other branch',async({page})=>{
  let outcome=200;
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    return route.fulfill({status:outcome,contentType:'application/json',
      body:outcome===200?JSON.stringify(body(
        u.searchParams.get('fromMonth')??'',
        u.searchParams.get('toMonth')??'',Number(u.searchParams.get('offset')??0),
      )):'{"code":"FORBIDDEN"}'});
  });
  await page.goto('/staff/auto-stock-abc-xyz');
  await scope(page);
  const load=page.getByRole('button',{name:'Построить ABC/XYZ'});
  await page.getByLabel('С месяца').fill('2030-01');
  await page.getByLabel('По месяц').fill('2030-02');
  await expect(load).toBeDisabled();
  const months=completedMonths();
  await page.getByLabel('С месяца').fill(months[0]);
  await page.getByLabel('По месяц').fill(months[2]);
  await load.click();
  await expect(page.getByLabel('Результаты ABC XYZ')).toBeVisible();
  await page.getByLabel('UUID филиала').fill(wrong);
  await expect(page.getByLabel('Результаты ABC XYZ')).toHaveCount(0);
  outcome=403;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Результаты ABC XYZ')).toHaveCount(0);
  outcome=409;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('слишком много SKU');
  await expect(page.getByLabel('Результаты ABC XYZ')).toHaveCount(0);
});

test('manager ABC/XYZ rejects inconsistent and tampered item response fail closed',async({page})=>{
  let malformed=false;
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    const result=body(u.searchParams.get('fromMonth')??'',
      u.searchParams.get('toMonth')??'',0);
    if(malformed)result.items[0].monthlyIssued=[0,0,0];
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(result)});
  });
  await page.goto('/staff/auto-stock-abc-xyz');
  await scope(page);
  const months=completedMonths();
  await page.getByLabel('С месяца').fill(months[0]);
  await page.getByLabel('По месяц').fill(months[2]);
  const load=page.getByRole('button',{name:'Построить ABC/XYZ'});
  await load.click();
  await expect(page.getByLabel('Результаты ABC XYZ')).toBeVisible();
  malformed=true;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить ABC/XYZ');
  await expect(page.getByLabel('Результаты ABC XYZ')).toHaveCount(0);
});


test('ABC/XYZ never rejects a locally closed month by using UTC browser time',async({page})=>{
  let calls=0;
  await page.route(routePath,route=>{
    calls++;
    return route.fulfill({status:422,contentType:'application/json',
      body:'{"code":"INVALID_INPUT"}'});
  });
  await page.goto('/staff/auto-stock-abc-xyz');
  await scope(page);
  const now=new Date();
  const utcIndex=now.getUTCFullYear()*12+now.getUTCMonth();
  await page.getByLabel('С месяца').fill(monthLabel(utcIndex-2));
  await page.getByLabel('По месяц').fill(monthLabel(utcIndex));
  // In a west-of-UTC branch the current UTC month may still be the
  // previous, already closed, local month. Only the server knows.
  const load=page.getByRole('button',{name:'Построить ABC/XYZ'});
  await expect(load).toBeEnabled();
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Нужны от 3 до 12 завершённых месяцев');
  expect(calls).toBe(1);
  await expect(page.getByLabel('Результаты ABC XYZ')).toHaveCount(0);
});


test('manager downloads complete scoped CSV and revoke blocks export fail closed',async({page})=>{
  const queried:string[]=[];
  let forbidden=false;
  await page.route(routePath,route=>{
    const u=new URL(route.request().url());
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(body(u.searchParams.get('fromMonth')??'',
        u.searchParams.get('toMonth')??'',0))});
  });
  await page.route('**/stock-abc-xyz/export?*',route=>{
    const u=new URL(route.request().url());
    queried.push(u.pathname+'?'+u.searchParams);
    if(forbidden)return route.fulfill({status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'});
    const lines=['SKU UUID;Артикул;Выдано',
      ...items.map(row=>row.skuId+';'+row.article+';'+row.grossIssued)];
    return route.fulfill({status:200,contentType:'text/csv; charset=utf-8',
      headers:{'content-disposition':'attachment; filename="auto-stock-abc-xyz.csv"'},
      body:'\ufeff'+lines.join('\r\n')});
  });
  await page.goto('/staff/auto-stock-abc-xyz');
  await scope(page);
  const months=completedMonths();
  await page.getByLabel('С месяца').fill(months[0]);
  await page.getByLabel('По месяц').fill(months[2]);
  await expect(page.getByLabel('С месяца')).toHaveValue(months[0]);
  await expect(page.getByLabel('По месяц')).toHaveValue(months[2]);
  const load=page.getByRole('button',{name:'Построить ABC/XYZ'});
  await expect(load).toBeEnabled();
  await load.click();
  const results=page.getByLabel('Результаты ABC XYZ');
  await expect(results).toBeVisible();
  await expect(results.getByRole('table').locator('tbody tr')).toHaveCount(25);
  const downloadPromise=page.waitForEvent('download');
  await results.getByRole('button',{name:'Скачать весь отчёт CSV'}).click();
  const download=await downloadPromise;
  expect(download.suggestedFilename()).toBe('auto-stock-abc-xyz.csv');
  expect(queried).toHaveLength(1);
  expect(queried[0]).toContain('/locations/'+loc+'/stock-abc-xyz/export?');
  expect(queried[0]).toContain('fromMonth='+months[0]);
  forbidden=true;
  await results.getByRole('button',{name:'Скачать весь отчёт CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Результаты ABC XYZ')).toHaveCount(0);
  expect(queried).toHaveLength(2);
});
