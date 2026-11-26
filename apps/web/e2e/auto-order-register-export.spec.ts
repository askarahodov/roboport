import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const appointment='44444444-4444-4444-8444-444444444444';
const bay='55555555-5555-4555-8555-555555555555';
const base='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders';
const item={workOrderId:order,appointmentId:appointment,bayId:bay,
  createdAt:'2026-11-18T10:00:00+00:00',
  startsAt:'2026-11-19T10:00:00+00:00',
  endsAt:'2026-11-19T11:00:00+00:00',stage:'DRAFT',repairStage:'DRAFT',documentStage:'RESTRICTED',bookingStatus:'SCHEDULED',localDay:'2026-11-19'};
const csv='\ufeffЗаказ-наряд;Запись;Пост;Создан;Начало записи;Окончание записи;Технический статус\r\n'+
  order+';'+appointment+';'+bay+';2026-11-18T10:00:00+00:00;'+
  '2026-11-19T10:00:00+00:00;2026-11-19T11:00:00+00:00;DRAFT\r\n';

async function scope(page:import('@playwright/test').Page){
  await page.goto('/staff/auto-order-register');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByRole('table')).toContainText(order);
}

test('CSV export uses current filter, downloads complete file at 390 and 1280',async({page})=>{
  const exports:URL[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname===base)return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,items:[item],nextCursor:null})});
    if(url.pathname===base+'/export'){
      exports.push(url);
      return route.fulfill({status:200,headers:{
        'Content-Type':'text/csv; charset=utf-8',
        'Content-Disposition':'attachment; filename="auto-work-order-register.csv"',
      },body:csv});
    }
    throw Error('unexpected URL '+url.pathname);
  });
  await page.setViewportSize({width:390,height:844});
  await scope(page);
  await page.getByRole('combobox',{name:'Состояние заказ-наряда'}).selectOption('DRAFT');
  await expect(page.getByRole('table')).toContainText(order);
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Скачать весь отфильтрованный реестр CSV'}).click();
  const result=await download;
  expect(result.suggestedFilename()).toBe('auto-work-order-register.csv');
  expect(exports).toHaveLength(1);
  expect(exports[0].searchParams.get('stage')).toBe('DRAFT');
  expect(exports[0].searchParams.get('view')).toBe('workflow');
  expect(exports[0].searchParams.get('documentStage')).toBe('ALL');
  expect(exports[0].searchParams.get('cursor')).toBeNull();
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.getByRole('textbox',{name:'Поиск заказ-наряда'}).fill(order);
  await page.getByRole('button',{name:'Найти в реестре'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  const second=page.waitForEvent('download');
  await page.getByRole('button',{name:'Скачать весь отфильтрованный реестр CSV'}).click();
  await second;
  expect(exports[1].searchParams.get('orderId')).toBe(order);
});

test('409 CSV limit makes no partial download; 403 removes previously visible rows',async({page})=>{
  let exportStatus=409;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname===base)return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,items:[item],nextCursor:null})});
    if(url.pathname===base+'/export')return route.fulfill(
      exportStatus===409?{status:409,contentType:'application/json',
        body:'{"code":"REGISTER_LIMIT"}'}:
        {status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
    throw Error('unexpected URL '+url.pathname);
  });
  await scope(page);
  await page.getByRole('button',{name:'Скачать весь отфильтрованный реестр CSV'}).click();
  await expect(page.getByText(/Экспорт превышает 1000 строк/)).toBeVisible();
  await expect(page.getByRole('table')).toContainText(order);
  exportStatus=403;
  await page.getByRole('button',{name:'Скачать весь отфильтрованный реестр CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('нет действующего доступа');
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Скачать весь отфильтрованный реестр CSV'})).toHaveCount(0);
});
