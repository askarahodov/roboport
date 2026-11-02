import { test, expect } from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const service='44444444-4444-4444-8444-444444444444';
const catalog=[{id:service,code:'SVC-TEST',title:'SYNTHETIC INSPECTION',durationMinutes:45}];
const base='**/api/v1/dev/auto/organizations/**';

test('manager selects catalog service on work order with CSRF, replay and responsive display',async({page})=>{
  let selected:typeof catalog=[];
  let postCount=0;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"test-csrf"}',
  }));
  await page.route(base,route=>{
    const request=route.request();
    const url=new URL(request.url());
    if(request.method()==='POST'){
      postCount++;
      expect(url.pathname).toContain('/orders/'+order+'/services');
      expect(request.headers()['x-csrf-token']).toBe('test-csrf');
      expect(request.postDataJSON()).toEqual({serviceId:service});
      selected=catalog;
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"writes":1,"replayed":false}'});
    }
    const items=url.pathname.endsWith('/service-catalog')?catalog:selected;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items})});
  });
  for(const width of [390,1280]){
    await page.setViewportSize({width,height:844});
    await page.goto('/staff/auto-order-services');
    await expect(page.getByRole('heading',{name:'Услуги заказ-наряда (dev)'})).toBeVisible();
    await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
    await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
    await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
    await expect(page.getByText('SVC-TEST — SYNTHETIC INSPECTION (45 мин)').first()).toBeVisible();
    if(!selected.length){
      await page.getByRole('button',{name:'Добавить услугу'}).click();
      await expect(page.getByRole('heading',{name:'Уже выбранные услуги'})).toBeVisible();
      await expect(page.getByRole('button',{name:'Уже добавлена'})).toBeDisabled();
    } else {
      await expect(page.getByRole('button',{name:'Уже добавлена'})).toBeDisabled();
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  expect(postCount).toBe(1);
});

test('service selector hides catalog and selected lines on permission revocation and tenant switch',async({page})=>{
  let denied=false;
  await page.route(base,route=>route.fulfill(denied?
    {status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
    {status:200,contentType:'application/json',body:JSON.stringify({items:catalog})}
  ));
  await page.goto('/staff/auto-order-services');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByText('SVC-TEST — SYNTHETIC INSPECTION (45 мин)').first()).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить список'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('SVC-TEST — SYNTHETIC INSPECTION (45 мин)')).toHaveCount(0);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill('77777777-7777-4777-8777-777777777777');
  await expect(page.getByRole('button',{name:'Добавить услугу'})).toHaveCount(0);
});
