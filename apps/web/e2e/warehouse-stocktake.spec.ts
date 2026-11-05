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


test('warehouse chooses scoped lot from real-shaped balance list and handles revoked grants',async({page})=>{
  const first='33333333-3333-4333-8333-333333333333';
  let denied=false;
  await page.route('**/api/v1/dev/auto/organizations/**/stocktake/lots?*',route=>{
    if(denied)return route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items:[{lotId:first,article:'SYN-33',batchCode:'LOT-1',
        binCode:'BIN-A',allocated:3,onHand:5,reserved:1}],nextLotId:null})});
  });
  await page.goto('/staff/warehouse-stocktake');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(location);
  await page.getByRole('button',{name:'Показать партии'}).click();
  await expect(page.getByText('SYN-33')).toBeVisible();
  await page.getByRole('button',{name:'Выбрать партию LOT-1'}).click();
  await expect(page.getByRole('textbox',{name:'UUID партии'})).toHaveValue(first);
  denied=true;
  await page.getByRole('button',{name:'Показать партии'}).click();
  await expect(page.getByRole('alert')).toContainText('Нет доступа');
  await expect(page.getByText('SYN-33')).toHaveCount(0);
});



test('manager reviews pending warehouse count, approves it, and sees fresh empty queue',async({page})=>{
  let hasCount=false;
  let approved=false;
  const approvalBodies:unknown[]=[];
  const countId='44444444-4444-4444-8444-444444444444';
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}'}));
  await page.route('**/stocktake/pending?*',route=>{
    const u=new URL(route.request().url());
    expect(u.searchParams.get('locationId')).toBe(location);
    const rows=hasCount&&!approved?[{
      countId,lotId:lot,article:'SYN-SKU',batchCode:'LOT-1',binCode:'BIN-A',
      expected:3,actual:2,currentAllocated:3,stale:false,
      reason:'Контрольный пересчёт',
    }]:[];
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items:rows,nextCountId:null,syntheticOnly:true})});
  });
  await page.route('**/stocktake/counts',route=>{
    hasCount=true;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({countId})});
  });
  await page.route('**/stocktake/approvals',route=>{
    approvalBodies.push(route.request().postDataJSON());
    approved=true;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({approvalId:approval})});
  });
  await page.goto('/staff/warehouse-stocktake');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(location);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('textbox',{name:'Причина пересчёта'}).fill('Контрольный пересчёт');
  await page.getByRole('button',{name:'Сохранить подсчёт'}).click();
  await expect(page.getByText('Подсчёт:',{exact:false})).toBeVisible();
  const queue=page.getByLabel('Ожидающие подсчёты филиала');
  await queue.getByRole('button',{name:'Обновить ожидающие подсчёты'}).click();
  await expect(queue).toContainText('SYN-SKU');
  await expect(queue).toContainText('учёт 3, факт 2, сейчас 3');
  await queue.getByRole('button',{name:'Выбрать подсчёт '+countId}).click();
  await expect(page.getByRole('textbox',{name:'UUID подсчёта'})).toHaveValue(countId);
  await page.getByRole('button',{name:'Утвердить корректировку'}).click();
  await expect(page.getByText('Расхождение утверждено.',{exact:false})).toBeVisible();
  expect(approvalBodies).toEqual([{locationId:location,countId}]);
  await expect(queue).not.toContainText('SYN-SKU');
  await queue.getByRole('button',{name:'Обновить ожидающие подсчёты'}).click();
  await expect(queue).toContainText('Ожидающих подсчётов нет.');
});

test('manager review queue blocks stale counts, revocation, and foreign branch disclosure',async({page})=>{
  const other='99999999-9999-4999-8999-999999999999';
  let denied=false;
  let invalid=false;
  await page.route('**/stocktake/pending?*',route=>{
    const u=new URL(route.request().url());
    if(denied||u.searchParams.get('locationId')!==location)
      return route.fulfill({status:403,contentType:'application/json',
        body:'{"code":"FORBIDDEN"}'});
    const items=[{
      countId:count,lotId:lot,article:'PRIVATE-STOCK',batchCode:'LOT-2',
      binCode:'BIN-A',expected:3,actual:2,currentAllocated:4,
      stale:invalid?false:true,reason:'Данные требуют пересчёта',
    }];
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items,nextCountId:null,syntheticOnly:true})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-stocktake');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(location);
  const queue=page.getByLabel('Ожидающие подсчёты филиала');
  await queue.getByRole('button',{name:'Обновить ожидающие подсчёты'}).click();
  await expect(queue).toContainText('PRIVATE-STOCK');
  await expect(queue.getByText('Подсчёт устарел: нужен новый пересчёт.')).toBeVisible();
  await expect(queue.getByRole('button',{name:'Выбрать подсчёт '+count})).toBeDisabled();
  invalid=true;
  await queue.getByRole('button',{name:'Обновить ожидающие подсчёты'}).click();
  await expect(queue.getByRole('alert')).toContainText('Не удалось загрузить очередь');
  await expect(queue).not.toContainText('PRIVATE-STOCK');
  invalid=false;
  denied=true;
  await queue.getByRole('button',{name:'Обновить ожидающие подсчёты'}).click();
  await expect(queue.getByRole('alert')).toContainText('Нет доступа к очереди');
  await expect(queue.getByRole('button',{name:'Выбрать подсчёт '+count})).toHaveCount(0);
  denied=false;
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(other);
  await expect(queue).not.toContainText('PRIVATE-STOCK');
  await queue.getByRole('button',{name:'Обновить ожидающие подсчёты'}).click();
  await expect(queue.getByRole('alert')).toContainText('Нет доступа к очереди');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
});
