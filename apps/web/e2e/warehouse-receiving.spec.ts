import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const lot='33333333-3333-4333-8333-333333333333';
const receipt='44444444-4444-4444-8444-444444444444';
const accepted='55555555-5555-4555-8555-555555555555';
const valuation='66666666-6666-4666-8666-666666666666';

test('warehouse receipt draft and independent acceptance use exact scope and distinct keys',async({page})=>{
  const seen:{url:string;key:string;body:any}[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/receipts**',route=>{
    const req=route.request();
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    seen.push({url:new URL(req.url()).pathname,key:req.headers()['idempotency-key'],
      body:req.postDataJSON()});
    const approve=req.url().endsWith('/accept');
    const quote=req.url().endsWith('/valuation');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify(quote?{valuationId:valuation}:approve?{acceptanceId:accepted}:{documentId:receipt})});
  });
  await page.goto('/staff/warehouse-receiving');
  await expect(page.getByRole('heading',{name:'Приход запчастей (dev)'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('spinbutton',{name:'Количество к приёмке'}).fill('4');
  await page.getByRole('textbox',{name:'Номер документа поставки'}).fill('SUP-REF-4');
  await page.getByRole('button',{name:'Создать документ'}).click();
  await expect(page.getByRole('status')).toContainText('Остатки пока не изменены');
  await expect(page.getByRole('textbox',{name:'UUID приходного документа'})).toHaveValue(receipt);
  await page.getByRole('button',{name:'Подтвердить приёмку'}).click();
  await expect(page.getByRole('status')).toContainText('Остатки обновлены');
  await page.getByRole('spinbutton',{name:'Закупочная цена за единицу'}).fill('1250');
  await page.getByRole('spinbutton',{name:'НДС по документу'}).fill('600');
  await page.getByRole('textbox',{name:'Ревизия цены поставщика'}).fill('QUOTE-REV-1');
  await page.getByRole('textbox',{name:'Время цены поставщика ISO UTC'}).fill('2026-10-28T12:00:00Z');
  await page.getByRole('button',{name:'Сохранить закупочную стоимость'}).click();
  await expect(page.getByRole('status')).toContainText('НДС зафиксированы отдельно');
  expect(seen).toHaveLength(3);
  expect(seen[0].url).toBe('/api/v1/dev/auto/organizations/'+org+'/warehouse/receipts');
  expect(seen[1].url).toBe('/api/v1/dev/auto/organizations/'+org+'/warehouse/receipts/accept');
  expect(seen[0].body).toEqual({locationId:loc,lotId:lot,quantity:4,sourceReference:'SUP-REF-4'});
  expect(seen[1].body).toEqual({locationId:loc,documentId:receipt});
  expect(seen[0].key).not.toBe(seen[1].key);
  expect(seen[1].key).not.toBe(seen[2].key);
  expect(seen[2].url).toBe('/api/v1/dev/auto/organizations/'+org+'/warehouse/receipts/valuation');
  expect(seen[2].body).toEqual({locationId:loc,documentId:receipt,currency:'RUB',
    unitNetMinor:1250,vatTotalMinor:600,supplierPriceRevision:'QUOTE-REV-1',
    supplierQuotedAt:'2026-10-28T12:00:00Z'});
});

test('warehouse receiving retries uncertain response using original idempotency key',async({page})=>{
  const keys:string[]=[];
  let first=true;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/receipts',route=>{
    keys.push(route.request().headers()['idempotency-key']);
    if(first){first=false;return route.fulfill({status:503,body:'Unavailable'});}
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({documentId:receipt})});
  });
  await page.goto('/staff/warehouse-receiving');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('textbox',{name:'Номер документа поставки'}).fill('SUP-REF-5');
  await page.getByRole('button',{name:'Создать документ'}).click();
  await expect(page.getByRole('status')).toContainText('Неизвестный результат');
  await expect(page.getByRole('button',{name:'Создать документ'})).toBeDisabled();
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('Документ создан');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test('warehouse receiving denies missing authority without false success',async({page})=>{
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/receipts',route=>
    route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}));
  await page.goto('/staff/warehouse-receiving');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('textbox',{name:'Номер документа поставки'}).fill('SUP-REF-6');
  await page.getByRole('button',{name:'Создать документ'}).click();
  await expect(page.getByRole('status')).toContainText('Нет прав');
  await expect(page.getByRole('textbox',{name:'UUID приходного документа'})).toHaveValue('');
});
