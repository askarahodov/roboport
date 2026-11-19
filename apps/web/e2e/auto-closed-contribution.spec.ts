import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const work='33333333-3333-4333-8333-333333333333';
const closed='44444444-4444-4444-8444-444444444444';
const posted='55555555-5555-4555-8555-555555555555';
const ack='66666666-6666-4666-8666-666666666666';
const preview='77777777-7777-4777-8777-777777777777';
const revision='88888888-8888-4888-8888-888888888888';
const cash='99999999-9999-4999-8999-999999999999';
const labor='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const service='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const approval='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const rate='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const sku='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const stock='ffffffff-ffff-4fff-8fff-ffffffffffff';
const lot='12121212-1212-4121-8121-121212121212';
const valuation='23232323-2323-4232-8232-232323232323';
const basis='CLOSED_TECHNICAL_QC_POSTED_SIM_SETTLEMENT_AND_PROVEN_DIRECT_COSTS';
const api='**/reports/closed-contribution?*';
const exportApi='**/reports/closed-contribution/export?*';
function report(fromDay:string,toDay:string,incomplete=false){
  const days=Array.from({length:(Date.parse(toDay+'T00:00:00Z')-
      Date.parse(fromDay+'T00:00:00Z'))/86400000+1},(_,i)=>new Date(
      Date.parse(fromDay+'T00:00:00Z')+i*86400000).toISOString().slice(0,10));
  const cost=incomplete?null:2751,material=incomplete?null:1250;
  const laborCost=incomplete?null:1501,diff=incomplete?null:-251;
  const item={
    workOrderId:work,closedDay:fromDay,closedEventId:closed,postedEventId:posted,
    ackEventId:ack,previewId:preview,previewDigest:'a'.repeat(64),
    estimateRevisionId:revision,status:incomplete?'INCOMPLETE':'COMPLETE',
    approvedTotalMinor:2500,simulatedPaidMinor:2500,simulatedRefundedMinor:0,
    simulatedNetMinor:2500,materialNetMinor:incomplete?1250:material,
    laborCostMinor:laborCost,directCostMinor:cost,syntheticDifferenceMinor:diff,
    cashEvents:[{id:cash,kind:'SIM_PAYMENT',amountMinor:2500,revisionId:revision}],
    laborEntries:[{laborEntryId:labor,serviceId:service,actualMinutes:45,
      status:incomplete?'NO_APPROVED_RATE':'TRACED',
      costMinor:laborCost,source:incomplete?null:{
        approvalId:approval,revisionId:rate,version:1,rateMinorPerHour:2001,
      }}],
    materialGroups:[{skuId:sku,lotId:lot,status:'TRACED',materialNetMinor:1250,
      source:{valuationId:valuation},
      movements:[{stockEventId:stock,action:'ISSUE',quantity:1,lotMovementId:lot}]}],
  };
  const unit=(i:number)=>({
    localDay:days[i],orderCount:i===0?1:0,
    completeOrderCount:i===0&&!incomplete?1:0,
    incompleteOrderCount:i===0&&incomplete?1:0,
    simCashEventCount:i===0?1:0,
    approvedTotalMinor:i===0?2500:0,
    simulatedPaidMinor:i===0?2500:0,simulatedRefundedMinor:0,
    simulatedNetMinor:i===0?2500:0,
    materialNetMinor:i===0?material:0,
    laborCostMinor:i===0?laborCost:0,
    directCostMinor:i===0?cost:0,
    syntheticDifferenceMinor:i===0?diff:0,
  });
  return {
    syntheticOnly:true,realMoney:false,releaseAllowed:false,
    marginAvailable:false,payrollAvailable:false,currency:'RUB',
    basis,timeZone:'Europe/Berlin',fromDay,toDay,
    summary:{orderCount:1,completeOrderCount:incomplete?0:1,
      incompleteOrderCount:incomplete?1:0,simCashEventCount:1,
      laborEntryCount:1,stockEventCount:1,
      approvedTotalMinor:2500,simulatedPaidMinor:2500,
      simulatedRefundedMinor:0,simulatedNetMinor:2500,
      materialNetMinor:material,laborCostMinor:laborCost,
      directCostMinor:cost,syntheticDifferenceMinor:diff},
    days:days.map((_,i)=>unit(i)),items:[item],
  };
}
async function fill(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}
test('448: руководитель проверяет полный QC → SIM cash → труд/склад по первичке 390/1280',async({page},info)=>{
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(p.get('fromDay')??'',p.get('toDay')??''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-closed-contribution');
  const root=page.getByLabel('Сверка закрытых заказ-нарядов и прямых затрат (dev)');
  await expect(root.getByRole('heading',{
    name:'Закрытые заказ-наряды: расчёты и подтверждённые затраты',
  })).toBeVisible();
  await expect(root).toContainText('не бухгалтерская');
  const load=page.getByRole('button',{name:'Сверить закрытые заказ-наряды'});
  await expect(load).toBeDisabled();
  await fill(page);
  await load.focus();await page.keyboard.press('Enter');
  const results=page.getByLabel('Результаты сверки закрытых заказ-нарядов');
  await expect(results.getByRole('status')).toContainText('условная разница: -2,51 RUB');
  await expect(results.getByRole('table',{name:'Период закрытия и доказанные суммы'})
    .locator('tbody tr')).toHaveCount(1);
  const order=results.locator('details').filter({has:page.locator('summary',{hasText:work})}).first();
  await order.locator('summary').first().click();
  await order.locator('details summary').filter({hasText:'Источники условных платежей'}).click();
  await order.locator('details summary').filter({hasText:'Доказательства труда'}).click();
  await order.locator('details summary').filter({hasText:'Доказательства материалов'}).click();
  for(const id of [cash,labor,approval,valuation,stock]){
    await expect(order).toContainText(id);
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('closed-contribution-390-light.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:info.outputPath('closed-contribution-1280-dark.png'),
    fullPage:true,animations:'disabled'});
});
test('448: incomplete первичка не становится нулём или ложной прибылью',async({page})=>{
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(p.get('fromDay')??'',p.get('toDay')??'',true))});
  });
  await page.goto('/staff/auto-closed-contribution');await fill(page);
  await page.getByRole('button',{name:'Сверить закрытые заказ-наряды'}).click();
  const results=page.getByLabel('Результаты сверки закрытых заказ-нарядов');
  await expect(results.getByRole('status')).toContainText('без полного доказательства: 1');
  await expect(results.getByRole('status')).toContainText('условная разница: Не определено');
  await expect(results).toContainText('Неполные первичные затраты');
});
test('448: испорченные суммы, дубли UUID, 403/409/422 сбрасывают историю',async({page})=>{
  let mode:'ok'|'tamper'|'duplicate'|'denied'|'limit'|'invalid'='ok';
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    const body=report(p.get('fromDay')??'',p.get('toDay')??'');
    if(mode==='tamper')body.summary.syntheticDifferenceMinor=999;
    if(mode==='duplicate')body.items[0].cashEvents[0].id=body.items[0].closedEventId;
    const status=mode==='denied'?403:mode==='limit'?409:mode==='invalid'?422:200;
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto('/staff/auto-closed-contribution');await fill(page);
  const load=page.getByRole('button',{name:'Сверить закрытые заказ-наряды'});
  await load.click();
  await expect(page.getByLabel('Результаты сверки закрытых заказ-нарядов')).toBeVisible();
  for(const [name,warning] of [
    ['tamper','Не удалось проверить'],
    ['limit','Конфликт снимка'],
    ['invalid','Неверный период'],
    ['denied','Доступ запрещён'],
  ] as const){
    mode=name;await load.click();
    await expect(page.getByRole('alert')).toContainText(warning);
    await expect(page.getByLabel('Результаты сверки закрытых заказ-нарядов')).toHaveCount(0);
    mode='ok';await load.click();
    await expect(page.getByLabel('Результаты сверки закрытых заказ-нарядов')).toBeVisible();
  }
  await page.getByLabel('UUID филиала').fill('34343434-3434-4343-8343-343434343434');
  await expect(page.getByLabel('Результаты сверки закрытых заказ-нарядов')).toHaveCount(0);
});
test('448: CSV fresh ACL check, denied and conflict invalidate rendered data',async({page})=>{
  let code:200|403|409=200;
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(report(p.get('fromDay')??'',p.get('toDay')??''))});
  });
  await page.route(exportApi,route=>route.fulfill(code===200?
    {status:200,contentType:'text/csv; charset=utf-8',
      body:'\ufeffДень;Уровень\r\n2026-11-19;ORDER\r\n'}:
    {status:code,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}));
  await page.goto('/staff/auto-closed-contribution');await fill(page);
  const load=page.getByRole('button',{name:'Сверить закрытые заказ-наряды'});
  await load.click();
  const results=page.getByLabel('Результаты сверки закрытых заказ-нарядов');
  const downloading=page.waitForEvent('download');
  await results.getByRole('button',{name:'Скачать сверку с UUID первички (CSV)'}).click();
  expect((await downloading).suggestedFilename()).toBe('auto-closed-contribution.csv');
  for(const [next,warning] of [[409,'Конфликт снимка'],[403,'Доступ запрещён']] as const){
    code=next;
    await results.getByRole('button',{name:'Скачать сверку с UUID первички (CSV)'}).click();
    await expect(page.getByRole('alert')).toContainText(warning);
    await expect(page.getByLabel('Результаты сверки закрытых заказ-нарядов')).toHaveCount(0);
    code=200;await load.click();
    await expect(page.getByLabel('Результаты сверки закрытых заказ-нарядов')).toBeVisible();
  }
});
