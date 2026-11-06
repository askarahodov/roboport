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
    if(req.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[],nextDocumentId:null}),
    });
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
  await expect(page.getByRole('status').filter({hasText:'закупочная оценка связаны'})).toContainText('закупочная оценка связаны');
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



test('manager sees return draft, approves, and receives refreshed empty queue',async({page})=>{
  let approved=false;
  const posts:unknown[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/supplier-returns**',
    route=>{
      const req=route.request();
      if(req.method()==='GET'){
        const url=new URL(req.url());
        expect(url.searchParams.get('locationId')).toBe(loc);
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify({syntheticOnly:true,nextDocumentId:null,
            items:approved?[]:[{documentId:doc,sourceReceiptId:receipt,
              lotId:'66666666-6666-4666-8666-666666666666',
              article:'SYN-SKU',batchCode:'LOT-A',binCode:'BIN-1',
              quantity:2,reason:'Возврат брака',supplierReference:'RMA-2026-001'}]})});
      }
      posts.push(req.postDataJSON());
      expect(req.headers()['x-csrf-token']).toBe('csrf');
      expect(req.headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/i);
      if(req.url().endsWith('/approvals'))approved=true;
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify(approved?{approvalId:approval}:{documentId:doc})});
    });
  await page.setViewportSize({width:390,height:844});
  await setup(page);
  const queue=page.getByLabel('Очередь актов поставщику');
  await queue.getByRole('button',{name:'Обновить акты к утверждению'}).click();
  await expect(queue).toContainText('SYN-SKU');
  await expect(queue).toContainText('RMA-2026-001');
  await queue.getByRole('button',{name:'Выбрать акт '+doc}).click();
  await expect(page.getByRole('textbox',{name:'UUID акта'})).toHaveValue(doc);
  await page.getByRole('button',{name:'Утвердить возврат'}).click();
  await expect(page.getByRole('status').filter({hasText:'Возврат поставщику утверждён'}))
    .toBeVisible();
  await expect(queue).toContainText('Актов к утверждению нет.');
  await expect(page.getByRole('textbox',{name:'UUID акта'})).toHaveValue('');
  expect(posts).toEqual([{locationId:loc,documentId:doc}]);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
});

test('supplier return review refuses stale/foreign location or malformed queue body',async({page})=>{
  let malformed=false;
  let deny=false;
  const foreign='77777777-7777-4777-8777-777777777777';
  await page.route('**/warehouse/supplier-returns/pending?*',route=>{
    const u=new URL(route.request().url());
    if(deny||u.searchParams.get('locationId')!==loc)
      return route.fulfill({status:403,contentType:'application/json',
        body:'{"code":"FORBIDDEN"}'});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,nextDocumentId:null,
        items:[{documentId:doc,sourceReceiptId:receipt,
          lotId:'66666666-6666-4666-8666-666666666666',
          article:'SECRET-SKU',batchCode:'LOT-A',binCode:'BIN-1',
          quantity:malformed?-1:2,reason:'Возврат брака',
          supplierReference:'RMA-2026-001'}]})});
  });
  await setup(page);
  const queue=page.getByLabel('Очередь актов поставщику');
  await queue.getByRole('button',{name:'Обновить акты к утверждению'}).click();
  await expect(queue.getByRole('button',{name:'Выбрать акт '+doc})).toBeVisible();
  malformed=true;
  await queue.getByRole('button',{name:'Обновить акты к утверждению'}).click();
  await expect(queue.getByRole('alert')).toContainText('Не удалось загрузить');
  await expect(queue).not.toContainText('SECRET-SKU');
  malformed=false;deny=true;
  await queue.getByRole('button',{name:'Обновить акты к утверждению'}).click();
  await expect(queue.getByRole('alert')).toContainText('Нет доступа');
  await expect(queue.getByRole('button',{name:'Выбрать акт '+doc})).toHaveCount(0);
  deny=false;
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(foreign);
  await expect(queue).not.toContainText('SECRET-SKU');
  await queue.getByRole('button',{name:'Обновить акты к утверждению'}).click();
  await expect(queue.getByRole('alert')).toContainText('Нет доступа');
});
