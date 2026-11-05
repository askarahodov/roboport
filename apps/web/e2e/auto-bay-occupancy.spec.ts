import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const base='**/api/v1/dev/auto/organizations/**/bay-occupancy*';

const report={syntheticOnly:true,basis:'CURRENT_BOOKINGS_CALENDAR_DAY',
  timeZone:'Europe/Berlin',bayCount:1,items:[{
    localDay:'2030-03-31',daySeconds:23*3600,activeAppointments:1,
    occupiedSeconds:3600,calendarCapacitySeconds:23*3600,occupancyBasisPoints:434,
    scheduledEmployees:2,scheduledShiftSeconds:8*3600,
    activeAssignments:1,assignedSeconds:3600,assignmentBasisPoints:1250,
    recordedLaborEntries:2,recordedLaborMinutes:73,finishedExecutions:1,
    createdAppointments:4,canceledAppointments:1,rescheduleEvents:2,
    scheduledStarts:3,scheduledCustomers:2,repeatBookingCustomers:1,
  }]};

test('manager report: scoped 390/1280 light/dark and no exposed customer fields',async({page})=>{
  const urls:string[]=[];
  await page.route(base,route=>{
    urls.push(route.request().url());
    expect(route.request().method()).toBe('GET');
    expect(route.request().headers()['cookie']).toBeUndefined();
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(report)});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-bay-occupancy');
  await expect(page.getByRole('heading',{name:'Загрузка постов по дням'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-03-31');
  await page.getByLabel('Конец периода').fill('2030-03-31');
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('table',{name:'Текущая занятость постов по локальным суткам'})).toBeVisible();
  await expect(page.getByRole('row',{name:/2030-03-31/})).toContainText('23');
  await expect(page.getByText('доступно постов: 1',{exact:false})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Часы смен'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Доля назначений от смен'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Минуты по журналу'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Завершено исполнений'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Переносы'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Повторные бронирования'})).toBeVisible();
  await expect(page.getByRole('row',{name:/2030-03-31/})).toContainText('73');
  await expect(page.getByRole('row',{name:/2030-03-31/})).toContainText('12,5 %');
  expect(urls).toHaveLength(1);
  const url=new URL(urls[0]);
  expect(url.pathname).toBe('/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/bay-occupancy');
  expect(url.searchParams.get('fromDay')).toBe('2030-03-31');
  expect(JSON.stringify(report).toLowerCase()).not.toContain('vin');
  await page.setViewportSize({width:1280,height:800});
  await expect(page.getByRole('heading',{name:'Загрузка постов по дням'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('manager report fails closed after branch switch or 403; no stale data',async({page})=>{
  let forbidden=false;
  await page.route(base,route=>route.fulfill({
    status:forbidden?403:200,contentType:'application/json',
    body:forbidden?'{"code":"FORBIDDEN"}':JSON.stringify(report),
  }));
  await page.goto('/staff/auto-bay-occupancy');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-03-31');
  await page.getByLabel('Конец периода').fill('2030-03-31');
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('33333333-3333-4333-8333-333333333333');
  await expect(page.getByRole('table')).toHaveCount(0);
  forbidden=true;
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('table')).toHaveCount(0);
  await page.getByLabel('Конец периода').fill('2030-06-01');
  await expect(page.getByRole('button',{name:'Сформировать отчёт'})).toBeDisabled();
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('report rejects malformed and out-of-scope responses without rendering bogus metrics',async({page})=>{
  await page.route(base,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({...report,items:[{...report.items[0],occupiedSeconds:-100}]}),
  }));
  await page.goto('/staff/auto-bay-occupancy');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-03-31');
  await page.getByLabel('Конец периода').fill('2030-03-31');
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить отчёт');
  await expect(page.getByRole('table')).toHaveCount(0);
});


test('shift report refuses self-consistent-looking but impossible assigned capacity',async({page})=>{
  await page.route(base,route=>route.fulfill({status:200,
    contentType:'application/json',body:JSON.stringify({
      ...report,items:[{...report.items[0],assignedSeconds:9*3600}],
    })}));
  await page.goto('/staff/auto-bay-occupancy');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-03-31');
  await page.getByLabel('Конец периода').fill('2030-03-31');
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить отчёт');
  await expect(page.getByRole('table')).toHaveCount(0);
});


test('manager labor metrics reject negative minutes and clear report after reload',async({page})=>{
  let invalid=false;
  await page.route(base,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify(invalid?{
      ...report,items:[{...report.items[0],recordedLaborMinutes:-1}],
    }:report),
  }));
  await page.goto('/staff/auto-bay-occupancy');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-03-31');
  await page.getByLabel('Конец периода').fill('2030-03-31');
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('table')).toBeVisible();
  invalid=true;
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить отчёт');
  await expect(page.getByRole('table')).toHaveCount(0);
});


test('visit funnel fails closed for impossible repeat-customer aggregate',async({page})=>{
  await page.route(base,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({...report,items:[{...report.items[0],
      repeatBookingCustomers:4}]}),
  }));
  await page.goto('/staff/auto-bay-occupancy');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-03-31');
  await page.getByLabel('Конец периода').fill('2030-03-31');
  await page.getByRole('button',{name:'Сформировать отчёт'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить отчёт');
  await expect(page.getByRole('table')).toHaveCount(0);
});
