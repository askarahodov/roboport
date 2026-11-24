import {test,expect} from '@playwright/test';
const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const sku='33333333-3333-4333-8333-333333333333';
const lot='44444444-4444-4444-8444-444444444444';
const event='55555555-5555-4555-8555-555555555555';
const stamp='2030-01-01T10:00:00Z';
test('warehouse reconciliation filters and drills down to movements, denies stale data',async({page})=>{
  let denied=false;
  const scoped={syntheticOnly:true,organizationId:org,locationId:loc};
  const row={skuId:sku,article:'SYN-PART',onHand:'2',reserved:'1',allocated:'2',unallocated:'0',available:'1',discrepancy:false};
  await page.route('**/warehouse/reconciliation**',route=>{
    const url=new URL(route.request().url());
    if(denied)return route.fulfill({status:403,contentType:'application/json',body:'{}'});
    expect(url.searchParams.get('locationId')).toBe(loc);
    if(url.pathname.endsWith('/'+sku)){
      const kind=url.searchParams.get('kind');
      const items=kind==='lots'?[{lotId:lot,createdAt:stamp,batchCode:'BATCH',binCode:'BIN',allocated:'2'}]:
        [{eventId:event,createdAt:stamp,action:'RETURN',quantity:'1'},
         {eventId:'66666666-6666-4666-8666-666666666666',createdAt:stamp,action:'RECLASS_IN',quantity:'1'},
         {eventId:'77777777-7777-4777-8777-777777777777',createdAt:stamp,action:'RECLASS_OUT',quantity:'1'}];
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...scoped,skuId:sku,kind,items,nextCursor:null})});
    }
    const empty=url.searchParams.get('status')==='DISCREPANCY';
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...scoped,
      canViewPurchases:false,summary:{total:1,discrepancies:0,unallocated:0,reserved:1,outOfStock:0,filtered:empty?0:1},
      items:empty?[]:[row],nextCursor:null})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-reconciliation');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByRole('button',{name:'Обновить сверку'}).click();
  await expect(page.getByText('Физический: 2 · резерв: 1 · размещено: 2 · не размещено: 0 · доступно: 1')).toBeVisible();
  await page.getByRole('button',{name:'Партии SYN-PART'}).click();
  await expect(page.getByText('BATCH · BIN · размещено 2')).toBeVisible();
  await page.getByRole('button',{name:'Движения SYN-PART'}).click();
  await expect(page.getByText('Событие '+event)).toBeVisible();
  await expect(page.getByText('Пересортица: приход · 1',{exact:false})).toBeVisible();
  await expect(page.getByText('Пересортица: расход · 1',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Приходы и оценка SYN-PART'})).toHaveCount(0);
  await page.getByLabel('Фильтр сверки').selectOption('DISCREPANCY');
  await page.getByRole('button',{name:'Обновить сверку'}).click();
  await expect(page.getByText('Нет артикулов по выбранному фильтру.')).toBeVisible();
  await page.getByLabel('Фильтр сверки').selectOption('ALL');
  await page.getByRole('button',{name:'Обновить сверку'}).click();
  denied=true;
  await page.getByRole('button',{name:'Движения SYN-PART'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ к сверке склада запрещён');
  await expect(page.getByRole('region',{name:'Сводка сверки'})).toHaveCount(0);
  await expect(page.getByText('Событие '+event)).toHaveCount(0);
});
