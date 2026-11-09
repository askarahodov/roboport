import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const sku='44444444-4444-4444-8444-444444444444';
const root='**/api/v1/dev/auto/organizations/**';

async function fillScope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
}

test('manager simulated reconciliation -> warehouse reserve -> sealed customer preview',async({page})=>{
  let paid=0,reserved=1,doc=false;
  const operations:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(root,route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(url.pathname.endsWith('/simulated-cash')){
        expect(body).toMatchObject({kind:'SIM_PAYMENT',amountMinor:2500});
        paid=2500;operations.push('PAYMENT');
      }else if(url.pathname.endsWith('/unused-reserve-release')){
        expect(body).toMatchObject({skuId:sku,quantity:1});
        reserved=0;operations.push('RELEASE');
      }else if(url.pathname.endsWith('/seal-preview')){
        expect(paid).toBe(2500);
        expect(reserved).toBe(0);
        doc=true;operations.push('SEAL');
      }
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"replayed":false}'});
    }
    const state={
      syntheticOnly:true,releaseAllowed:false,legalGatePassed:false,
      currency:'RUB',approvedVersion:1,approvedBaseMinor:2500,approvedExtraMinor:0,
      approvedExtraVersion:null,approvedExtraRevisionId:null,approvedTotalMinor:2500,
      simulatedPaidMinor:paid,simulatedRefundedMinor:0,simulatedDueMinor:2500-paid,
      events:paid?[{id:'55555555-5555-4555-8555-555555555555',kind:'SIM_PAYMENT',
        amountMinor:paid,createdAt:'2030-01-01T10:00:00Z'}]:[],
      outstandingReserves:reserved?[{skuId:sku,quantity:reserved}]:[],
      unreceivedMaterialCount:0,executionStatus:'QC_ACCEPTED',
      blockers:[...(reserved?['STOCK_RESERVATION_OPEN']:[]),
        ...(paid===0?['SIMULATED_BALANCE_OPEN']:[])],
      canSealPreview:paid===2500&&reserved===0&&!doc,
      preview:doc?{id:'66666666-6666-4666-8666-666666666666',
        revisionId:'77777777-7777-4777-8777-777777777777',
        digest:'f'.repeat(64),createdAt:'2030-01-01T11:00:00Z',
        snapshot:{approvedTotalMinor:2500,releaseAllowed:false}}:null,
    };
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(state)});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-close');
  await fillScope(page);
  await expect(page.getByRole('heading',{name:'Сверка сметы (RUB, копейки)'})).toBeVisible();
  await expect(page.getByText('Остаток: 2500',{exact:false})).toBeVisible();
  await expect(page.getByText('Есть неосвобождённый резерв склада')).toBeVisible();
  await page.getByRole('spinbutton',{name:'Сумма, коп.'}).fill('2500');
  await page.getByRole('button',{name:'Записать условное движение'}).click();
  await expect(page.getByText('Остаток: 0',{exact:false})).toBeVisible();
  await page.getByRole('combobox',{name:'SKU резерва'}).selectOption(sku);
  await page.getByRole('button',{name:'Освободить резерв'}).click();
  await expect(page.getByText('Технические проверки пройдены.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Зафиксировать технический пакет (MANAGER)'}).click();
  await expect(page.getByRole('heading',{name:'Технический пакет зафиксирован'})).toBeVisible();
  await expect(page.getByText('Разрешение на выдачу: НЕТ.',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:844});
  await page.goto('/account/auto-demo-close');
  await fillScope(page);
  await expect(page.getByRole('heading',{name:'Технический пакет зафиксирован'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Записать условное движение'})).toHaveCount(0);
  await expect(page.getByText('НЕ ПРОЙДЕН.',{exact:false})).toBeVisible();
  expect(operations).toEqual(['PAYMENT','RELEASE','SEAL']);
});

test('revoke clears financial and document details and tenant change remains hidden',async({page})=>{
  let revoked=false;
  await page.route(root,route=>route.fulfill(revoked?
    {status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
    {status:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,releaseAllowed:false,legalGatePassed:false,currency:'RUB',
      approvedVersion:1,approvedBaseMinor:37890,approvedExtraMinor:0,
      approvedExtraVersion:null,approvedExtraRevisionId:null,approvedTotalMinor:37890,simulatedPaidMinor:0,
      simulatedRefundedMinor:0,simulatedDueMinor:37890,events:[],
      outstandingReserves:[],unreceivedMaterialCount:0,executionStatus:'QC_ACCEPTED',
      blockers:['SIMULATED_BALANCE_OPEN'],canSealPreview:false,preview:null,
    })}
  ));
  await page.goto('/staff/auto-demo-close');
  await fillScope(page);
  await expect(page.getByText('Основные работы: 37890',{exact:false})).toBeVisible();
  revoked=true;
  await page.getByRole('button',{name:'Обновить сверку'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('Смета: 37890',{exact:false})).toHaveCount(0);
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('99999999-9999-4999-8999-999999999999');
  await expect(page.getByRole('button',{name:'Зафиксировать технический пакет (MANAGER)'})).toHaveCount(0);
});


test('additional CLIENT consent increases synthetic balance and never authorizes release',async({page},testInfo)=>{
  let paid=0,sealed=false;
  const events:number[]=[];
  const revision='77777777-7777-4777-8777-777777777777';
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route(root,route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const body=req.postDataJSON();
      if(url.pathname.endsWith('/simulated-cash')){
        expect(body.kind).toBe('SIM_PAYMENT');
        expect(body.amountMinor).toBe(13751);
        paid+=body.amountMinor;events.push(paid);
      }else if(url.pathname.endsWith('/seal-preview')){
        expect(paid).toBe(13751);
        sealed=true;
      }else throw Error('Unexpected operation '+url.pathname);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"replayed":false}'});
    }
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({
        syntheticOnly:true,releaseAllowed:false,legalGatePassed:false,
        currency:'RUB',approvedVersion:1,
        approvedBaseMinor:2500,approvedExtraMinor:11251,
        approvedExtraRevisionId:revision,approvedExtraVersion:1,
        approvedTotalMinor:13751,simulatedPaidMinor:paid,simulatedRefundedMinor:0,
        simulatedDueMinor:13751-paid,events:[],outstandingReserves:[],
        unreceivedMaterialCount:0,executionStatus:'QC_ACCEPTED',
        blockers:paid===13751?[]:['SIMULATED_BALANCE_OPEN'],
        canSealPreview:paid===13751&&!sealed,
        preview:sealed?{id:sku,revisionId:revision,digest:'a'.repeat(64),
          createdAt:'2030-01-01T09:00:00Z',
          snapshot:{approvedBaseMinor:2500,approvedExtraMinor:11251,
            approvedTotalMinor:13751,releaseAllowed:false}}:null,
      })});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-close');
  await fillScope(page);
  await expect(page.getByText('Основные работы: 2500',{exact:false})).toBeVisible();
  await expect(page.getByText('Согласованные допработы: 11251',{exact:false})).toBeVisible();
  await expect(page.getByText('Итого по согласованным работам: 13751',{exact:false})).toBeVisible();
  await expect(page.getByText('Остаток: 13751',{exact:false})).toBeVisible();
  for(const mode of ['light','dark'] as const){
    await page.emulateMedia({colorScheme:mode});
    await page.screenshot({path:testInfo.outputPath(
      `settlement-390-${mode}.png`),fullPage:true});
  }
  await page.getByRole('spinbutton',{name:'Сумма, коп.'}).fill('13751');
  await page.getByRole('button',{name:'Записать условное движение'}).click();
  await expect(page.getByText('Остаток: 0',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Зафиксировать технический пакет (MANAGER)'}).click();
  await expect(page.getByRole('heading',{name:'Технический пакет зафиксирован'})).toBeVisible();
  await expect(page.getByText('Разрешение на выдачу: НЕТ.',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:800});
  await page.goto('/account/auto-demo-close');
  await fillScope(page);
  await expect(page.getByText('Согласованные допработы: 11251',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Записать условное движение'})).toHaveCount(0);
  await expect(page.getByText('НЕ ПРОЙДЕН.',{exact:false})).toBeVisible();
  for(const mode of ['light','dark'] as const){
    await page.emulateMedia({colorScheme:mode});
    await page.screenshot({path:testInfo.outputPath(
      `settlement-1280-${mode}.png`),fullPage:true});
  }
  expect(events).toEqual([13751]);
});
