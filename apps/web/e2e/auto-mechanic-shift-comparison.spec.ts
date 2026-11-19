import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const worker='33333333-3333-4333-8333-333333333333';
const idle='44444444-4444-4444-8444-444444444444';
const shift1='55555555-5555-4555-8555-555555555555';
const shift2='66666666-6666-4666-8666-666666666666';
const api='**/reports/mechanic-productivity/shift-comparison?*';
const exportApi='**/reports/mechanic-productivity/shift-comparison/export?*';

function report(fromDay:string,toDay:string){
  return {
    syntheticOnly:true,realMoney:false,payrollAvailable:false,releaseAllowed:false,
    basis:'CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES',
    periodBasis:'CLOSED_TECHNICAL_LOCAL_DAY',timeZone:'Europe/Berlin',
    fromDay,toDay,summary:{
      mechanicCount:1,workOrderCount:1,serviceCount:1,actualMinutes:45,
    },
    items:[{mechanicId:worker,actualMinutes:45}],
    shiftComparison:{
      basis:'ACTIVE_SHIFTS_VS_CLOSED_TECHNICAL_LABOR',
      isAttendance:false,isPayroll:false,isEfficiencyScore:false,limitedToShifts:500,
      summary:{mechanicCount:2,shiftedMechanicCount:2,shiftCount:2,
        scheduledSeconds:10800,recordedMinutes:45},
      items:[
        {mechanicId:worker,recordedMinutes:45,scheduledSeconds:7200,shiftCount:1,
          recordedToScheduledBasisPoints:3750,status:'COMPARABLE',
          shifts:[{shiftId:shift1,startsAt:fromDay+'T08:00:00Z',
            endsAt:fromDay+'T10:00:00Z',overlapSeconds:7200}]},
        {mechanicId:idle,recordedMinutes:0,scheduledSeconds:3600,shiftCount:1,
          recordedToScheduledBasisPoints:0,status:'NO_CLOSED_LABOR',
          shifts:[{shiftId:shift2,startsAt:fromDay+'T10:00:00Z',
            endsAt:fromDay+'T11:00:00Z',overlapSeconds:3600}]},
      ],
    },
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}

test('448: менеджер сравнивает труд и смены, видит простои и первичку на 390/1280',async({page},testInfo)=>{
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(p.get('fromDay')??'',p.get('toDay')??''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-mechanic-shifts');
  const root=page.getByLabel('Сопоставление смен и закрытых работ (dev)');
  await expect(root.getByRole('heading',{name:'Смены и проверенная выработка механиков'}))
    .toBeVisible();
  await expect(root).toContainText('не измеряет присутствие, KPI, зарплату');
  const button=root.getByRole('button',{name:'Сопоставить смены и труд'});
  await expect(button).toBeDisabled();
  await scope(page);
  await button.focus();await page.keyboard.press('Enter');
  const results=page.getByLabel('Результаты сверки смен');
  await expect(results.getByRole('status')).toContainText('подтверждённый труд: 45 минут');
  await expect(results.getByRole('table').locator('tbody tr')).toHaveCount(2);
  await expect(results.getByRole('table')).toContainText('Нет технически закрытого труда');
  await results.getByText('Источники смен:',{exact:false}).first().click();
  await expect(results).toContainText(shift1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.screenshot({path:testInfo.outputPath('shift-comparison-390-light.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('shift-comparison-1280-dark.png'),
    fullPage:true,animations:'disabled'});
});

test('448: повреждённые суммы, 409, 403 и смена филиала скрывают старую сверку',async({page})=>{
  let mode:'ok'|'bad'|'denied'|'limit'='ok';
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    const body=report(p.get('fromDay')??'',p.get('toDay')??'');
    if(mode==='bad')body.shiftComparison.summary.scheduledSeconds=1;
    return route.fulfill({status:mode==='denied'?403:mode==='limit'?409:200,
      contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto('/staff/auto-mechanic-shifts');
  await scope(page);
  const load=page.getByRole('button',{name:'Сопоставить смены и труд'});
  await load.click();
  await expect(page.getByLabel('Результаты сверки смен')).toBeVisible();
  mode='bad';await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось сформировать');
  await expect(page.getByLabel('Результаты сверки смен')).toHaveCount(0);
  mode='limit';await load.click();
  await expect(page.getByRole('alert')).toContainText('500 записей');
  mode='denied';await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  mode='ok';await load.click();
  await expect(page.getByLabel('Результаты сверки смен')).toBeVisible();
  await page.getByLabel('UUID филиала').fill('77777777-7777-4777-8777-777777777777');
  await expect(page.getByLabel('Результаты сверки смен')).toHaveCount(0);
});

test('448: экспорт ещё раз проверяет ACL и отказывается после отзыва',async({page})=>{
  let exportStatus:200|403|409|422=200;
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(p.get('fromDay')??'',p.get('toDay')??''))});
  });
  await page.route(exportApi,route=>route.fulfill(exportStatus!==200?
    {status:exportStatus,contentType:'application/json',body:'{"code":"DENIED_OR_CONFLICT"}'}:
    {status:200,contentType:'text/csv; charset=utf-8',
      body:'\ufeffМеханик UUID;Уровень\r\n'+worker+';MECHANIC\r\n'}));
  await page.goto('/staff/auto-mechanic-shifts');
  await scope(page);
  await page.getByRole('button',{name:'Сопоставить смены и труд'}).click();
  const results=page.getByLabel('Результаты сверки смен');
  const download=page.waitForEvent('download');
  await results.getByRole('button',{name:'Скачать сверку CSV'}).click();
  expect((await download).suggestedFilename()).toBe('auto-mechanic-shift-comparison.csv');
  for(const [code,text] of [[409,'Конфликт'],[422,'Неверный период'],[403,'Доступ запрещён']] as const){
    exportStatus=code;
    await results.getByRole('button',{name:'Скачать сверку CSV'}).click();
    await expect(page.getByRole('alert')).toContainText(text);
    await expect(page.getByLabel('Результаты сверки смен')).toHaveCount(0);
    exportStatus=200;
    if(code!==403){
      await page.getByRole('button',{name:'Сопоставить смены и труд'}).click();
      await expect(page.getByLabel('Результаты сверки смен')).toBeVisible();
    }
  }
});
