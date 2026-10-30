import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const receipt='33333333-3333-4333-8333-333333333333';
const doc='44444444-4444-4444-8444-444444444444';
const approval='55555555-5555-4555-8555-555555555555';

async function setup(page:Page){
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.goto('/staff/warehouse-supplier-returns');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID принятого прихода'}).fill(receipt);
  await page.getByRole('spinbutton',{name:'Количество'}).fill('2');
  await page.getByRole('textbox',{name:'Причина'}).fill('Возврат брака');
  await page.getByRole('textbox',{name:'Номер обращения поставщику'}).fill('RMA-2026-001');
}

test('supplier return prepares, approves and preserves provenance request bodies',async({page})=>{
  const sent:{url:string;key:string;body:unknown}[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/supplier-returns**',route=>{
    const req=route.request();
    expect(req.headers()['x-csrf-token']).toBe('csrf');
    sent.push({url:new URL(req.url()).pathname,
      key:req.headers()['idempotency-key'],body:req.postDataJSON()});
    const approved=req.url().endsWith('/approvals');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify(approved?{approvalId:approval}:{documentId:doc})});
  });
  await setup(page);
  await page.getByRole('button',{name:'Создать акт возврата'}).click();
  await expect(page.getByRole('status')).toContainText('остаток ещё не изменён');
  await expect(page.getByRole('textbox',{name:'UUID акта'})).toHaveValue(doc);
  await page.getByRole('button',{name:'Утвердить возврат'}).click();
  await expect(page.getByRole('status')).toContainText('закупочная оценка связаны');
  expect(sent).toHaveLength(2);
  expect(sent[0].body).toEqual({locationId:loc,sourceReceiptId:receipt,
    quantity:2,reason:'Возврат брака',supplierReference:'RMA-2026-001'});
  expect(sent[1].body).toEqual({locationId:loc,documentId:doc});
  expect(sent[0].key).not.toBe(sent[1].key);
});

test('supplier return holds key and request after in-flight scope edit',async({page})=>{
  const keys:string[]=[];
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/supplier-returns**',
    async route=>{
      const req=route.request();
      keys.push(req.headers()['idempotency-key']);
      if(keys.length===1){await gate;return route.fulfill({status:503,body:'error'});}
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({documentId:doc})});
    });
  await setup(page);
  const issued=page.waitForRequest('**/warehouse/supplier-returns');
  await page.getByRole('button',{name:'Создать акт возврата'}).click();
  await issued;
  await page.getByRole('textbox',{name:'UUID филиала'}).fill('77777777-7777-4777-8777-777777777777');
  release();
  await expect(page.getByRole('alert')).toContainText('Неизвестный результат');
  await expect(page.getByRole('button',{name:'Создать акт возврата'})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Повторить с прежним ключом'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('Акт возврата поставщику создан');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
