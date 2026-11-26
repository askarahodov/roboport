import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const lot='33333333-3333-4333-8333-333333333333';
const order='44444444-4444-4444-8444-444444444444';
const purchase='55555555-5555-4555-8555-555555555555';
const documentId='66666666-6666-4666-8666-666666666666';
const deliveryId='77777777-7777-4777-8777-777777777777';
const cancelId='88888888-8888-4888-8888-888888888888';
const urlPart='/api/v1/dev/auto/organizations/'+org+'/warehouse/supplier-orders';

async function fillOrder(page: import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByLabel('UUID заказ-наряда').fill(order);
  await page.getByLabel('UUID партии').fill(lot);
  await page.getByLabel('Артикул SKU').fill('SYNTH-001');
  await page.getByLabel('Требуется для работы (шт.)').fill('5');
}
function item(documented:number,canceled=0){
  return {orderId:purchase,workOrderId:order,lotId:lot,article:'SYNTH-001',
    orderedQuantity:4,supplierId:'SYNTH-SUP-2',offerId:'OFFLINE-OFFER-2',
    quoteRevision:'SYNTHETIC-QUOTE-2',purchaseUnitMinor:1350,currency:'RUB',
    documentedQuantity:documented,acceptedQuantity:0,valuedQuantity:0,
    canceledQuantity:canceled,outstandingQuantity:4-documented-canceled};
}

test('compare two quotes → buy shortage → partial delivery → cancel only outstanding, mobile',async({page})=>{
  const changes:{url:string;body:any;key:string}[]=[];
  let documented=0,canceled=0;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/warehouse/supplier-orders**',route=>{
    const req=route.request();
    const path=new URL(req.url()).pathname;
    if(req.method()==='GET'){
      expect(path).toBe(urlPart);
      expect(new URL(req.url()).searchParams.get('locationId')).toBe(loc);
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,nextOrderId:null,
          items:changes.some(x=>x.url===urlPart)?[item(documented,canceled)]:[]})});
    }
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    const key=req.headers()['idempotency-key'];
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    const body=req.postDataJSON();
    changes.push({url:path,body,key});
    if(path.endsWith('/receipts')){
      documented+=body.quantity;
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,deliveryId,documentId})});
    }
    if(path.endsWith('/cancel')){
      canceled=4-documented;
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,cancelId,canceledQuantity:canceled})});
    }
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,orderId:purchase,orderedQuantity:4})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-supplier-orders');
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await fillOrder(page);
  await page.getByRole('radio',{name:'Выбрать предложение 2'}).check();
  await page.getByRole('button',{name:'Создать заказ поставщику'}).click();
  await expect(page.getByRole('status').filter({hasText:'Заказ создан'})).toBeVisible();
  expect(changes).toHaveLength(1);
  expect(changes[0].body.selectedSupplierId).toBe('SYNTH-SUP-2');
  expect(changes[0].body.selectedOfferId).toBe('OFFLINE-OFFER-2');
  expect(changes[0].body.offers).toHaveLength(2);
  expect(changes[0].body.offers[0].delivery_days).toBe(2);
  expect(changes[0].body.offers[1].delivery_days).toBe(1);
  expect(changes[0].body.desiredQuantity).toBe(5);
  expect(changes[0].body.offers[1].currency).toBe('RUB');
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await expect(page.getByRole('button',{name:'Выбрать заказ '+purchase})).toBeVisible();
  await page.getByRole('button',{name:'Выбрать заказ '+purchase}).click();
  await page.getByLabel('Количество текущей поставки').fill('2');
  await page.getByRole('button',{name:'Принять частичную поставку в черновик'}).click();
  await expect(page.getByRole('status')).toContainText('Другой MANAGER отдельно подтвердит');
  expect(changes[1].url).toBe(urlPart+'/'+purchase+'/receipts');
  expect(changes[1].body).toEqual({
    locationId:loc,quantity:2,supplierReference:'SYNTH-DELIVERY-1',
  });
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await page.getByRole('button',{name:'Выбрать заказ '+purchase}).click();
  await page.getByLabel('Подтверждаю отмену только непоставленного остатка заказа').check();
  await page.getByRole('button',{name:'Отменить непоставленный остаток'}).click();
  await expect(page.getByRole('status')).toContainText('остаток заказа отменён');
  expect(changes[2].url).toBe(urlPart+'/'+purchase+'/cancel');
  expect(changes[2].body).toEqual({locationId:loc});
  expect(new Set(changes.map(x=>x.key)).size).toBe(3);
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await expect(page.getByText('к поставке 0')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('unknown POST result retries same UUID key and unchanged immutable snapshot',async({page})=>{
  let first=true;
  const requests:{key:string;body:string}[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/warehouse/supplier-orders',route=>{
    const req=route.request();requests.push({
      key:req.headers()['idempotency-key'],body:req.postData()||'',
    });
    if(first){first=false;return route.fulfill({status:503,body:'Unavailable'});}
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,orderId:purchase,orderedQuantity:4})});
  });
  await page.goto('/staff/warehouse-supplier-orders');
  await fillOrder(page);
  await page.getByRole('button',{name:'Создать заказ поставщику'}).click();
  await expect(page.getByRole('alert')).toContainText('Повторить с прежним ключом');
  await expect(page.getByRole('button',{name:'Создать заказ поставщику'})).toBeDisabled();
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('Заказ создан');
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
});

test('403 read clears list and does not show supplier cost from former branch',async({page})=>{
  let denied=false;
  await page.route('**/warehouse/supplier-orders?*',route=>route.fulfill(denied?
    {status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
    {status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[item(0)],nextOrderId:null})}));
  await page.goto('/staff/warehouse-supplier-orders');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await expect(page.getByText('1350 коп./шт.',{exact:false})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await expect(page.getByRole('alert')).toContainText('403/404');
  await expect(page.getByText('1350 коп./шт.',{exact:false})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Принять частичную поставку в черновик'})).toBeDisabled();
});

test('staff role navigation exposes supplier orders only for stock or manager',async({page})=>{
  let roles=['STOCK'];
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({roles}),
  }));
  await page.goto('/staff');
  const nav=page.getByRole('navigation',{name:'Навигация СТО'});
  await expect(nav.getByRole('link',{name:'Заказы поставщикам (dev)'})).toHaveAttribute(
    'href','/staff/warehouse-supplier-orders');
  roles=['ENGINEER'];await page.reload();
  await expect(nav.getByRole('link',{name:'Заказы поставщикам (dev)'})).toHaveCount(0);
  roles=['MANAGER'];await page.reload();
  await expect(nav.getByRole('link',{name:'Заказы поставщикам (dev)'})).toBeVisible();
});


test('purchase lifecycle card, filtered register, hidden comparison and receipt handoff',async({page})=>{
  let hidden=false,denied=false;
  const calls:URL[]=[];
  await page.route('**/warehouse/supplier-orders**',route=>{
    const url=new URL(route.request().url());calls.push(url);
    const row={...item(2),purchasePriceHidden:hidden,purchaseUnitMinor:hidden?null:1350};
    if(url.pathname===urlPart)return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[row],nextOrderId:null})});
    expect(url.pathname).toBe(urlPart+'/'+purchase);
    if(denied)return route.fulfill({status:403,body:'{}'});
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,order:row,desiredQuantity:5,availableAtOrder:1,
      createdAt:'2030-01-01T00:00:00Z',quotedAt:'2030-01-01T00:00:00Z',expiresAt:'2030-01-08T00:00:00Z',
      offers:hidden?null:[{supplier_id:'A',offer_id:'A1',source_revision:'R1',currency:'RUB',unit_purchase_minor:1200,available_quantity:4,delivery_days:2},
        {supplier_id:'B',offer_id:'B1',source_revision:'R2',currency:'RUB',unit_purchase_minor:1350,available_quantity:4,delivery_days:1}],
      deliveries:[{deliveryId,documentId,quantity:2,createdAt:'2030-01-02T00:00:00Z',stage:'DRAFT'}],nextDeliveryId:null})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-supplier-orders');
  await fillOrder(page);
  await page.getByLabel('Состояние закупки').selectOption('AWAITING_ACCEPTANCE');
  await page.getByLabel('Фильтр по UUID заказ-наряда').fill(order);
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await page.getByRole('button',{name:'Выбрать заказ '+purchase}).click();
  expect(calls[0].searchParams.get('state')).toBe('AWAITING_ACCEPTANCE');
  expect(calls[0].searchParams.get('workOrderId')).toBe(order);
  await page.getByRole('button',{name:'Открыть карточку закупки'}).click();
  const card=page.getByRole('region',{name:'Карточка закупки'});
  await expect(card).toContainText('1200 minor');
  await expect(card).toContainText(documentId);
  hidden=true;
  await page.getByRole('button',{name:'Обновить карточку'}).click();
  await expect(card).toContainText('Закупочные цены и сравнение скрыты');
  await expect(page.getByText('1350 коп./шт.',{exact:false})).toHaveCount(0);
  await expect(card.getByRole('link',{name:'К приёмке и оценке'})).toHaveAttribute('href',
    '/staff/warehouse-receiving?org='+org+'&loc='+loc+'&documentId='+documentId);
  denied=true;
  await page.getByRole('button',{name:'Обновить карточку'}).click();
  await expect(page.getByRole('alert')).toContainText('403/404');
  await expect(card).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Выбрать заказ '+purchase})).toHaveCount(0);
});

test('receipt handoff carries only identifiers and never submits on navigation',async({page})=>{
  let writes=0;
  await page.route('**/warehouse/receipts**',route=>{
    if(route.request().method()==='POST')writes++;
    return route.fulfill({status:403,body:'{}'});
  });
  await page.goto('/staff/warehouse-receiving?org='+org+'&loc='+loc+'&documentId='+documentId);
  await expect(page.getByLabel('UUID приходного документа')).toHaveValue(documentId);
  expect(writes).toBe(0);
});


test('branch change during CSRF never dispatches old procurement mutation',async({page})=>{
  let started=false,posts=0;
  let release!:()=>void;
  const held=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/v1/auth/csrf',async route=>{
    started=true;await held;
    await route.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}).catch(()=>{});
  });
  await page.route('**/warehouse/supplier-orders**',route=>{
    if(route.request().method()==='POST')posts++;
    return route.fulfill({status:403,body:'{}'});
  });
  await page.goto('/staff/warehouse-supplier-orders');
  await fillOrder(page);
  await page.getByRole('button',{name:'Создать заказ поставщику'}).click();
  await expect.poll(()=>started).toBe(true);
  try{
    await page.getByLabel('UUID филиала').fill('99999999-9999-4999-8999-999999999999');
    await page.getByLabel('UUID филиала').fill(loc);
    await expect(page.getByRole('button',{name:'Создать заказ поставщику'})).toBeEnabled();
  }finally{release();}
  await page.getByRole('button',{name:'Обновить заказы'}).click();
  await expect(page.getByRole('alert')).toContainText('403/404');
  expect(posts).toBe(0);
});
