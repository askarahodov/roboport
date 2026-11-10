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
    if(req.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,stage:new URL(req.url()).searchParams.get('stage'),
        items:[],nextDocumentId:null}),
    });
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
  await expect(page.getByRole('status').filter({hasText:'Остатки пока не изменены'})).toContainText('Остатки пока не изменены');
  await expect(page.getByRole('textbox',{name:'UUID приходного документа'})).toHaveValue(receipt);
  await page.getByRole('button',{name:'Подтвердить приёмку'}).click();
  await expect(page.getByRole('status').filter({hasText:'Остатки обновлены'})).toContainText('Остатки обновлены');
  await page.getByRole('spinbutton',{name:'Закупочная цена за единицу'}).fill('1250');
  await page.getByRole('spinbutton',{name:'НДС по документу'}).fill('600');
  await page.getByRole('textbox',{name:'Ревизия цены поставщика'}).fill('QUOTE-REV-1');
  await page.getByRole('textbox',{name:'Время цены поставщика ISO UTC'}).fill('2026-10-28T12:00:00Z');
  await page.getByRole('button',{name:'Сохранить закупочную стоимость'}).click();
  await expect(page.getByRole('status').filter({hasText:'НДС зафиксированы отдельно'})).toContainText('НДС зафиксированы отдельно');
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
  await expect(page.getByRole('status').filter({hasText:'Неизвестный результат'})).toContainText('Неизвестный результат');
  await expect(page.getByRole('button',{name:'Создать документ'})).toBeDisabled();
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status').filter({hasText:'Документ создан'})).toContainText('Документ создан');
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
  await expect(page.getByRole('status').filter({hasText:'Нет прав'})).toContainText('Нет прав');
  await expect(page.getByRole('textbox',{name:'UUID приходного документа'})).toHaveValue('');
});



test('manager reviews receipt draft, accepts and values from live queues',async({page})=>{
  let acceptedOnce=false;
  let valuedOnce=false;
  const queries:string[]=[];
  const mutations:{path:string;body:any;key:string}[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route('**/warehouse/receipts/pending?*',route=>{
    const u=new URL(route.request().url());
    const stage=u.searchParams.get('stage');
    queries.push(stage??'');
    expect(u.searchParams.get('locationId')).toBe(loc);
    const show=stage==='accept'?!acceptedOnce:acceptedOnce&&!valuedOnce;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,stage,nextDocumentId:null,
        items:show?[{documentId:receipt,lotId:lot,article:'SYN-120',
          batchCode:'LOT-A',binCode:'BIN-1',quantity:4,
          sourceReference:'SUP-QUEUE-4'}]:[]})});
  });
  await page.route('**/warehouse/receipts/accept',route=>{
    const req=route.request();
    mutations.push({path:new URL(req.url()).pathname,key:req.headers()['idempotency-key'],
      body:req.postDataJSON()});
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    acceptedOnce=true;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({acceptanceId:accepted})});
  });
  await page.route('**/warehouse/receipts/valuation',route=>{
    const req=route.request();
    mutations.push({path:new URL(req.url()).pathname,key:req.headers()['idempotency-key'],
      body:req.postDataJSON()});
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    valuedOnce=true;
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({valuationId:valuation})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-receiving');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  const drafts=page.getByLabel('Документы на подтверждение прихода');
  const values=page.getByLabel('Принятые документы без оценки');
  await drafts.getByRole('button',{name:'Показать черновики'}).click();
  await expect(drafts).toContainText('SUP-QUEUE-4');
  await drafts.getByRole('button',{name:'Выбрать черновик '+receipt}).click();
  await expect(page.getByRole('textbox',{name:'UUID приходного документа'})).toHaveValue(receipt);
  await page.getByRole('button',{name:'Подтвердить приёмку'}).click();
  await expect(page.getByText('Приёмка подтверждена другим сотрудником.',{
    exact:false})).toBeVisible();
  await expect(drafts).toContainText('Черновиков к приёмке нет.');
  await expect(values).toContainText('SUP-QUEUE-4');
  await values.getByRole('button',{name:'Выбрать для оценки '+receipt}).click();
  await page.getByRole('spinbutton',{name:'Закупочная цена за единицу'}).fill('1250');
  await page.getByRole('spinbutton',{name:'НДС по документу'}).fill('600');
  await page.getByRole('textbox',{name:'Ревизия цены поставщика'}).fill('QUOTE-QUEUE-001');
  await page.getByRole('textbox',{name:'Время цены поставщика ISO UTC'}).fill('2026-10-28T12:00:00Z');
  await page.getByRole('button',{name:'Сохранить закупочную стоимость'}).click();
  await expect(page.getByText('Закупочная стоимость и НДС зафиксированы',{
    exact:false})).toBeVisible();
  await expect(values).toContainText('Документов без оценки нет.');
  expect(mutations).toHaveLength(2);
  expect(mutations[0].body).toEqual({locationId:loc,documentId:receipt});
  expect(mutations[1].body.documentId).toBe(receipt);
  expect(mutations[0].key).not.toBe(mutations[1].key);
  expect(queries).toContain('accept');expect(queries).toContain('value');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
});

test('receipt manager queue fails closed on foreign branch, revoke and malformed JSON',async({page})=>{
  const foreign='99999999-9999-4999-8999-999999999999';
  let bad=false;
  await page.route('**/warehouse/receipts/pending?*',route=>{
    const u=new URL(route.request().url());
    if(u.searchParams.get('locationId')!==loc)return route.fulfill({
      status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,stage:u.searchParams.get('stage'),
        nextDocumentId:null,items:[{documentId:receipt,lotId:lot,
          article:'PRIVATE-SUPPLIER',batchCode:'LOT-A',binCode:'BIN-1',
          quantity:4,sourceReference:'SUP-QUEUE-4',
          ...(bad?{quantity:-4}:{})}]})});
  });
  await page.goto('/staff/warehouse-receiving');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  const drafts=page.getByLabel('Документы на подтверждение прихода');
  await drafts.getByRole('button',{name:'Показать черновики'}).click();
  await expect(drafts).toContainText('PRIVATE-SUPPLIER');
  bad=true;
  await drafts.getByRole('button',{name:'Показать черновики'}).click();
  await expect(drafts.getByRole('alert')).toContainText('Не удалось загрузить черновики');
  await expect(drafts).not.toContainText('PRIVATE-SUPPLIER');
  bad=false;
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(foreign);
  await expect(drafts).not.toContainText('PRIVATE-SUPPLIER');
  await drafts.getByRole('button',{name:'Показать черновики'}).click();
  await expect(drafts.getByRole('alert')).toContainText('Нет доступа');
  await expect(drafts.getByRole('button',{name:'Выбрать черновик '+receipt})).toHaveCount(0);
});


test('manager picks exact immutable purchase quote from pending valuation and keeps VAT separate',async({page})=>{
  const purchase='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const quotedAt='2026-11-10T06:00:00.123+00:00';
  const price={orderId:purchase,supplierId:'SYNTH-SUP-1',offerId:'OFFLINE-1',
    currency:'RUB',unitNetMinor:1250,supplierPriceRevision:'SOURCE-REV-9',
    supplierQuotedAt:quotedAt};
  const payloads:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/warehouse/receipts/pending?*',route=>{
    const stage=new URL(route.request().url()).searchParams.get('stage');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,stage,nextDocumentId:null,
        items:stage==='value'?[{documentId:receipt,lotId:lot,article:'SYN-120',
          batchCode:'LOT-A',binCode:'BIN-1',quantity:2,sourceReference:'SUP-9',
          purchaseQuote:price}]:[]})});
  });
  await page.route('**/warehouse/receipts/valuation',route=>{
    payloads.push(route.request().postDataJSON());
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({valuationId:valuation})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-receiving');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  const queue=page.getByLabel('Принятые документы без оценки');
  await queue.getByRole('button',{name:'Показать документы без оценки'}).click();
  await expect(queue).toContainText('SOURCE-REV-9');
  await expect(queue).toContainText(quotedAt);
  await queue.getByRole('button',{name:'Выбрать для оценки '+receipt}).click();
  await expect(page.getByRole('status')).toContainText(purchase);
  const unit=page.getByRole('spinbutton',{name:'Закупочная цена за единицу'});
  const revision=page.getByRole('textbox',{name:'Ревизия цены поставщика'});
  const timestamp=page.getByRole('textbox',{name:'Время цены поставщика ISO UTC'});
  await expect(unit).toHaveValue('1250');
  await expect(unit).toHaveJSProperty('readOnly',true);
  await expect(revision).toHaveValue('SOURCE-REV-9');
  await expect(timestamp).toHaveValue(quotedAt);
  await expect(timestamp).toHaveJSProperty('readOnly',true);
  await page.getByRole('spinbutton',{name:'НДС по документу'}).fill('250');
  await page.getByRole('button',{name:'Сохранить закупочную стоимость'}).click();
  await expect(page.getByRole('status').filter({hasText:'НДС зафиксированы отдельно'})).toBeVisible();
  expect(payloads).toEqual([{locationId:loc,documentId:receipt,currency:'RUB',
    unitNetMinor:1250,vatTotalMinor:250,supplierPriceRevision:'SOURCE-REV-9',
    supplierQuotedAt:quotedAt}]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});


test('revoked manager receipt access clears previously displayed immutable supplier quote',async({page})=>{
  let denied=false;
  const quotedAt='2026-11-10T06:00:00.123+00:00';
  await page.route('**/warehouse/receipts/pending?*',route=>{
    const stage=new URL(route.request().url()).searchParams.get('stage');
    return route.fulfill(denied?{status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'}:{
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,stage,nextDocumentId:null,
        items:stage==='value'?[{documentId:receipt,lotId:lot,article:'SYN-120',
          batchCode:'LOT-A',binCode:'BIN-1',quantity:2,sourceReference:'SUP-9',
          purchaseQuote:{orderId:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            supplierId:'SYNTH-SUP-1',offerId:'OFFLINE-1',currency:'RUB',
            unitNetMinor:1250,supplierPriceRevision:'SECRET-SYNTH-REV',
            supplierQuotedAt:quotedAt}}]:[]})});
  });
  await page.goto('/staff/warehouse-receiving');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  const queue=page.getByLabel('Принятые документы без оценки');
  const load=queue.getByRole('button',{name:'Показать документы без оценки'});
  await load.click();
  await queue.getByRole('button',{name:'Выбрать для оценки '+receipt}).click();
  await expect(page.getByRole('textbox',{name:'Ревизия цены поставщика'})).toHaveValue('SECRET-SYNTH-REV');
  denied=true;
  await load.click();
  await expect(queue.getByRole('alert')).toContainText('Нет доступа');
  await expect(queue).not.toContainText('SECRET-SYNTH-REV');
  await expect(page.getByRole('textbox',{name:'Ревизия цены поставщика'})).toHaveValue('');
  await expect(page.getByRole('spinbutton',{name:'Закупочная цена за единицу'})).toHaveValue('0');
  await expect(page.getByRole('status').filter({hasText:'Цена и время котировки'})).toHaveCount(0);
});
