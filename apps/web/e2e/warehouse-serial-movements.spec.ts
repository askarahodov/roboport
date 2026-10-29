import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const serial='33333333-3333-4333-8333-333333333333';
const order='44444444-4444-4444-8444-444444444444';
const destination='55555555-5555-4555-8555-555555555555';
const movement='66666666-6666-4666-8666-666666666666';

async function fill(page:Page){
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.goto('/staff/warehouse-serial-movements');
  await expect(page.getByRole('heading',{name:'Движения серийных деталей (dev)'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID исходного филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID серийной детали'}).fill(serial);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await page.getByRole('textbox',{name:'UUID партии назначения'}).fill(destination);
}

test('serial issue return and interbranch movement keep distinct exact ledger identities',async({page})=>{
  const seen:{action:string;key:string;body:unknown}[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/serial-movements/**',route=>{
    const req=route.request();
    expect(req.headers()['x-csrf-token']).toBe('csrf');
    const action=new URL(req.url()).pathname.split('/').pop()!;
    seen.push({action,key:req.headers()['idempotency-key'],body:req.postDataJSON()});
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({movementId:movement})});
  });
  await fill(page);
  await page.getByRole('button',{name:'Выдать серийную деталь'}).click();
  await expect(page.getByRole('status')).toContainText('выдана по заказ-наряду');
  await page.getByRole('button',{name:'Вернуть серийную деталь'}).click();
  await expect(page.getByRole('status')).toContainText('возвращена в исходную партию');
  await page.getByRole('button',{name:'Переместить серийную деталь'}).click();
  await expect(page.getByRole('status')).toContainText('в другой филиал');
  expect(seen.map(x=>x.action)).toEqual(['issue','return','transfer']);
  expect(seen[0].body).toEqual({locationId:loc,serialId:serial,workOrderId:order});
  expect(seen[1].body).toEqual({locationId:loc,serialId:serial,workOrderId:order});
  expect(seen[2].body).toEqual({locationId:loc,serialId:serial,destinationLotId:destination});
  expect(new Set(seen.map(x=>x.key)).size).toBe(3);
});

test('serial transfer retains request key on ambiguous response and branch edit',async({page})=>{
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  const keys:string[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/serial-movements/transfer',
    async route=>{
      keys.push(route.request().headers()['idempotency-key']);
      if(keys.length===1){await gate;return route.fulfill({status:503,body:'error'});}
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({movementId:movement})});
    });
  await fill(page);
  const sent=page.waitForRequest('**/warehouse/serial-movements/transfer');
  await page.getByRole('button',{name:'Переместить серийную деталь'}).click();
  await sent;
  await page.getByRole('textbox',{name:'UUID исходного филиала'}).fill(
    '77777777-7777-4777-8777-777777777777');
  release();
  await expect(page.getByRole('alert')).toContainText('Результат операции не определён');
  await expect(page.getByRole('button',{name:'Переместить серийную деталь'})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Повторить с прежним ключом'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID исходного филиала'}).fill(loc);
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('в другой филиал');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
