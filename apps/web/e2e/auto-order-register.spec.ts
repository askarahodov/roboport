import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const order2='44444444-4444-4444-8444-444444444444';
const booking='55555555-5555-4555-8555-555555555555';
const bay='66666666-6666-4666-8666-666666666666';
const base='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc;
const row=(id:string)=>({workOrderId:id,appointmentId:booking,bayId:bay,
  createdAt:'2026-11-18T10:00:00+00:00',startsAt:'2026-11-19T10:00:00+00:00',
  endsAt:'2026-11-19T11:00:00+00:00',stage:'DRAFT'});

test('order register: scoped lookup, page navigation and link to selected order',async({page})=>{
  const requests:URL[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url()),path=url.pathname;
    if(path===base+'/orders'){
      requests.push(url);
      const filtered=url.searchParams.get('orderId');
      const stage=url.searchParams.get('stage');
      const cursor=url.searchParams.get('cursor');
      const records=stage==='READY'?[]:filtered?[row(filtered)]:
        cursor?[row(order2)]:[row(order)];
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
          items:records,nextCursor:cursor||filtered||stage==='READY'?null:'signed-cursor'})});
    }
    if(path===base+'/service-catalog'||path===base+'/orders/'+order+'/services'){
      return route.fulfill({status:200,contentType:'application/json',
        body:'{"items":[]}'});
    }
    throw Error('unexpected URL '+url);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-order-register');
  await expect(page.getByRole('heading',{name:'Реестр заказ-нарядов — DEV'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('button',{name:'Следующая страница'})).toBeVisible();
  await page.getByRole('button',{name:'Следующая страница'}).click();
  await expect(page.getByRole('table')).toContainText(order2);
  await page.getByRole('button',{name:'Предыдущая страница'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByRole('combobox',{name:'Состояние заказ-наряда'}).selectOption('READY');
  await expect(page.getByText('Заказ-наряды по этому фильтру не найдены.')).toBeVisible();
  await page.getByRole('combobox',{name:'Состояние заказ-наряда'}).selectOption('ALL');
  await page.getByRole('textbox',{name:'Поиск заказ-наряда'}).fill(order);
  await page.getByRole('button',{name:'Найти в реестре'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByRole('button',{name:'Сведения'}).click();
  await expect(page.getByRole('region',{name:'Сведения о выбранном заказ-наряде'})).toContainText(booking);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.getByRole('link',{name:'Открыть рабочее место выбранного наряда'}).click();
  await expect(page.getByRole('heading',{name:'Заказ-наряд: единое рабочее место (dev)'})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await expect(page.getByRole('textbox',{name:'UUID организации'})).toHaveValue(org);
  await expect(page.getByRole('textbox',{name:'UUID филиала'})).toHaveValue(loc);
  expect(requests.some(x=>x.searchParams.get('stage')==='READY')).toBe(true);
  expect(requests.some(x=>x.searchParams.get('cursor')==='signed-cursor')).toBe(true);
});

test('revoked register denies and clears previously shown order IDs',async({page})=>{
  let revoked=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname!==base+'/orders')throw Error('unexpected route '+url);
    return route.fulfill(revoked?{status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'}:{
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
        items:[row(order)],nextCursor:null}),
    });
  });
  await page.goto('/staff/auto-order-register');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByRole('table')).toContainText(order);
  revoked=true;
  await page.getByRole('button',{name:'Обновить реестр'}).click();
  await expect(page.getByRole('alert')).toContainText('нет действующего доступа');
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByText(order,{exact:true})).toHaveCount(0);
});
