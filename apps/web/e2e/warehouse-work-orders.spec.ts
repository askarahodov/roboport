import {test,expect} from '@playwright/test';
const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const lot='44444444-4444-4444-8444-444444444444';
const issued='55555555-5555-4555-8555-555555555555';
const returned='66666666-6666-4666-8666-666666666666';
async function fill(page:import('@playwright/test').Page){
  await page.goto('/staff/warehouse-work-orders');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('spinbutton',{name:'Количество'}).fill('2');
}
test('dev warehouse issue and return have independent idempotency keys and correct scope',async({page})=>{
  const seen:{url:string;key:string;body:unknown}[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/work-orders/**',route=>{
    const request=route.request();
    expect(request.headers()['x-csrf-token']).toBe('csrf');
    seen.push({url:new URL(request.url()).pathname,key:request.headers()['idempotency-key'],
      body:request.postDataJSON()});
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify(request.url().endsWith('/issue')?{issueId:issued}:{returnId:returned})});
  });
  await fill(page);
  await page.getByRole('button',{name:'Выдать из партии'}).click();
  await expect(page.getByRole('status')).toContainText('Выдача из партии проведена');
  await page.getByRole('button',{name:'Вернуть в партию'}).click();
  await expect(page.getByRole('status')).toContainText('Возврат в исходную партию');
  expect(seen).toHaveLength(2);
  expect(seen.map(x=>x.url)).toEqual([
    '/api/v1/dev/auto/organizations/'+org+'/warehouse/work-orders/issue',
    '/api/v1/dev/auto/organizations/'+org+'/warehouse/work-orders/return']);
  expect(seen[0].body).toEqual({locationId:loc,workOrderId:order,lotId:lot,quantity:2});
  expect(seen[1].body).toEqual(seen[0].body);
  expect(seen[0].key).not.toBe(seen[1].key);
});
test('unknown outcome locks both actions and retries the exact original request',async({page})=>{
  const keys:string[]=[];let count=0;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/work-orders/issue',route=>{
    keys.push(route.request().headers()['idempotency-key']);
    count++;
    return route.fulfill(count===1?{status:503,body:'unavailable'}:
      {status:201,contentType:'application/json',body:JSON.stringify({issueId:issued})});
  });
  await fill(page);
  await page.getByRole('button',{name:'Выдать из партии'}).click();
  await expect(page.getByRole('status')).toContainText('Неизвестный результат');
  await expect(page.getByRole('button',{name:'Вернуть в партию'})).toBeDisabled();
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('Выдача из партии проведена');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
test('denied warehouse issue does not announce success',async({page})=>{
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/work-orders/issue',
    route=>route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}));
  await fill(page);
  await page.getByRole('button',{name:'Выдать из партии'}).click();
  await expect(page.getByRole('status')).toContainText('Нет доступа');
  await expect(page.getByText('Идентификатор события:')).toHaveCount(0);
});
