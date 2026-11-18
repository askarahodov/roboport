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
  await page.getByRole('textbox',{name:'UUID серийной детали',exact:true}).fill(serial);
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

test('independent serial receipt proof through warehouse UI, role denial and mobile/dark',async({page},testInfo)=>{
  const receipt='88888888-8888-4888-8888-888888888888';
  const attestation='99999999-9999-4999-8999-999999999999';
  const approval='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const item={assertionId:attestation,serialId:serial,receiptId:receipt,
    locationId:loc,assertedAt:'2030-10-27T09:00:00+00:00',
    approvalId:null as string|null,approvedAt:null as string|null,syntheticOnly:true};
  let items:typeof item[]=[],status=200;
  const keys:string[]=[];
  await page.route('**/warehouse/serial-receipts*',route=>{
    const req=route.request();
    const isApprove=req.url().includes('/approve');
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('csrf');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      keys.push(body.requestKey);
      if(isApprove){
        items=[{...item,approvalId:approval,approvedAt:'2030-10-27T09:01:00+00:00'}];
      }else{
        expect(body.serialId).toBe(serial);
        expect(body.receiptId).toBe(receipt);
        items=[item];
      }
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify(items[0])});
    }
    return route.fulfill({status,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items})});
  });
  await page.setViewportSize({width:390,height:844});
  await fill(page);
  const section=page.getByRole('region',{name:'Привязка серийных деталей к приходам'});
  await section.getByLabel('UUID серийной детали для первички').fill(serial);
  await section.getByLabel('UUID принятого прихода').fill(receipt);
  await section.getByRole('button',{name:'Связать серию с приходом'}).click();
  await expect(section.getByRole('status')).toContainText('Операция подтверждена');
  await section.getByRole('button',{name:'Прочитать подтверждения серийных деталей'}).click();
  await expect(section.getByText(/Ожидает независимого утверждения/)).toBeVisible();
  await section.getByRole('button',{name:'Утвердить привязку '+attestation}).click();
  await section.getByRole('button',{name:'Прочитать подтверждения серийных деталей'}).click();
  await expect(section.getByText(/Подтверждено/)).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).not.toBe(keys[1]);
  await page.screenshot({path:testInfo.outputPath('serial-receipts-mobile-light.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('serial-receipts-desktop-light.png'),
    fullPage:true,animations:'disabled'});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('serial-receipts-desktop-dark.png'),
    fullPage:true,animations:'disabled'});
  status=403;
  await section.getByRole('button',{name:'Прочитать подтверждения серийных деталей'}).click();
  await expect(section.getByRole('alert')).toContainText('Нет доступа');
  await expect(section.getByText(/Подтверждено/)).toHaveCount(0);
});

test('serial receipt ambiguous post preserves requestKey across location edits',async({page})=>{
  const receipt='88888888-8888-4888-8888-888888888888';
  const attestation='99999999-9999-4999-8999-999999999999';
  const keys:string[]=[];
  await page.route('**/warehouse/serial-receipts',route=>{
    const req=route.request();
    keys.push(req.postDataJSON().requestKey);
    if(keys.length===1)return route.fulfill({status:503,body:'ambiguous'});
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({assertionId:attestation,serialId:serial,receiptId:receipt,
        locationId:loc,assertedAt:'2030-10-27T09:00:00Z',
        approvalId:null,approvedAt:null,syntheticOnly:true})});
  });
  await fill(page);
  const section=page.getByRole('region',{name:'Привязка серийных деталей к приходам'});
  await section.getByLabel('UUID серийной детали для первички').fill(serial);
  await section.getByLabel('UUID принятого прихода').fill(receipt);
  await section.getByRole('button',{name:'Связать серию с приходом'}).click();
  await expect(section.getByRole('alert')).toContainText('Результат записи неизвестен');
  const retry=section.getByRole('button',{name:'Повторить запись с тем же ключом'});
  await expect(retry).toBeEnabled();
  await page.getByRole('textbox',{name:'UUID исходного филиала'}).fill(destination);
  await expect(retry).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID исходного филиала'}).fill(loc);
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(section.getByRole('status')).toContainText('Операция подтверждена');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
