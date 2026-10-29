import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const location='22222222-2222-4222-8222-222222222222';
const lot='33333333-3333-4333-8333-333333333333';
const count='44444444-4444-4444-8444-444444444444';
const approval='55555555-5555-4555-8555-555555555555';

test('warehouse recount and independent approval use exact scoped routes and distinct IDs',async({page})=>{
  const requests:{path:string;key:string;body:any}[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/stocktake/**',route=>{
    const req=route.request();
    requests.push({path:new URL(req.url()).pathname,key:req.headers()['idempotency-key'],
      body:req.postDataJSON()});
    expect(req.headers()['x-csrf-token']).toBe('synthetic');
    const isCount=req.url().endsWith('/counts');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify(isCount?{countId:count}:{approvalId:approval})});
  });
  await page.goto('/staff/warehouse-stocktake');
  await expect(page.getByRole('heading',{name:'Инвентаризация склада (dev)'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(location);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('spinbutton',{name:'Фактическое количество'}).fill('3');
  await page.getByRole('textbox',{name:'Причина пересчёта'}).fill('Контрольный пересчёт');
  await page.getByRole('button',{name:'Сохранить подсчёт'}).click();
  await expect(page.getByRole('status')).toContainText('Подсчёт сохранён');
  await expect(page.getByRole('textbox',{name:'UUID подсчёта'})).toHaveValue(count);
  await page.getByRole('button',{name:'Утвердить корректировку'}).click();
  await expect(page.getByRole('status')).toContainText('Расхождение утверждено');
  expect(requests).toHaveLength(2);
  expect(requests[0].path).toBe('/api/v1/dev/auto/organizations/'+org+'/stocktake/counts');
  expect(requests[0].body).toEqual({locationId:location,lotId:lot,actual:3,reason:'Контрольный пересчёт'});
  expect(requests[1].path).toBe('/api/v1/dev/auto/organizations/'+org+'/stocktake/approvals');
  expect(requests[1].body).toEqual({locationId:location,countId:count});
  expect(requests[0].key).not.toBe(requests[1].key);
});

test('warehouse unexpected result retries same idempotency key without double submit',async({page})=>{
  const keys:string[]=[];
  let first=true;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/stocktake/counts',route=>{
    keys.push(route.request().headers()['idempotency-key']);
    if(first){first=false;return route.fulfill({status:503,body:'Unavailable'});}
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({countId:count})});
  });
  await page.goto('/staff/warehouse-stocktake');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(location);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('textbox',{name:'Причина пересчёта'}).fill('Повторный контроль');
  await page.getByRole('button',{name:'Сохранить подсчёт'}).click();
  await expect(page.getByRole('status')).toContainText('Результат неизвестен');
  await expect(page.getByRole('button',{name:'Сохранить подсчёт'})).toBeDisabled();
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('Подсчёт сохранён');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test('warehouse denied response cannot be presented as successful recount',async({page})=>{
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/stocktake/counts',route=>
    route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}));
  await page.goto('/staff/warehouse-stocktake');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(location);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('textbox',{name:'Причина пересчёта'}).fill('Отказ в доступе');
  await page.getByRole('button',{name:'Сохранить подсчёт'}).click();
  await expect(page.getByRole('status')).toContainText('Доступ запрещён');
  await expect(page.getByRole('textbox',{name:'UUID подсчёта'})).toHaveValue('');
});
