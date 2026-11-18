import {test,expect} from '@playwright/test';
const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const bay='66666666-6666-4666-8666-666666666666';
const appt='55555555-5555-4555-8555-555555555555';
const base='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc;
const url='/staff/auto-order-workspace?'+new URLSearchParams({org,loc,order});
const response={syntheticOnly:true,releaseAllowed:false,nextCursor:null,items:[{
  workOrderId:order,appointmentId:appt,bayId:bay,stage:'DRAFT',
  startsAt:'2026-11-20T10:00:00+00:00',endsAt:'2026-11-20T11:00:00+00:00'}]};

test('work-order workspace reuses eight guarded operations without losing selected order',async({page})=>{
  let checked=0;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const address=new URL(route.request().url());
    if(address.pathname===base+'/orders'){
      checked++;
      expect(address.searchParams.get('orderId')).toBe(order);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(response)});
    }
    if(address.pathname===base+'/service-catalog'||
       address.pathname===base+'/orders/'+order+'/services'){
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    }
    // Individual operations must enforce their own roles. 403 is expected
    // when INTAKE navigates to a manager-only stage.
    return route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto(url);
  await expect(page.getByRole('heading',{name:'Заказ-наряд: единое рабочее место (dev)'})).toBeVisible();
  await expect(page.getByRole('navigation',{name:'Этапы заказ-наряда'})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await page.getByRole('button',{name:'5. Смета и согласование'}).click();
  await expect(page.getByRole('navigation',{name:'Этапы заказ-наряда'})).toBeVisible();
  await expect(page).toHaveURL(/step=estimate/);
  await page.getByRole('button',{name:'7. Выполнение и материалы'}).click();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await page.getByRole('button',{name:'8. Условная сверка и документы'}).click();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await expect(page.getByText('Юридическое закрытие и физическая выдача:')).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.getByRole('button',{name:'1. Работы'}).click();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  expect(checked).toBeGreaterThan(0);
});

test('workspace revocation and invalid identity clear and block all operations',async({page})=>{
  let denied=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const address=new URL(route.request().url());
    if(address.pathname===base+'/orders')return route.fulfill(denied?{
      status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'
    }:{status:200,contentType:'application/json',body:JSON.stringify(response)});
    return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
  });
  await page.goto(url);
  await expect(page.getByRole('navigation',{name:'Этапы заказ-наряда'})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Повторно проверить доступ и состояние'}).click();
  await expect(page.getByRole('alert')).toContainText('Заказ-наряд недоступен');
  await expect(page.getByRole('navigation',{name:'Этапы заказ-наряда'})).toHaveCount(0);
  await page.goto('/staff/auto-order-workspace?org=forged&loc='+loc+'&order='+order);
  await expect(page.getByRole('alert')).toContainText('Не указан корректный UUID');
  await expect(page.getByRole('navigation',{name:'Этапы заказ-наряда'})).toHaveCount(0);
});
