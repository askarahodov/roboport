import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const close='44444444-4444-4444-8444-444444444444';
const preview='55555555-5555-4555-8555-555555555555';
const revision='66666666-6666-4666-8666-666666666666';
const account='77777777-7777-4777-8777-777777777777';
const labor='88888888-8888-4888-8888-888888888888';
const approval='99999999-9999-4999-8999-999999999999';
const sku='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const lot='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const valuation='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const receipt='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const ids=['eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
  'ffffffff-ffff-4fff-8fff-ffffffffffff',
  '11111111-2222-4333-8444-555555555555',
  '22222222-3333-4444-8555-666666666666',
  '33333333-4444-4555-8666-777777777777'];
const api='**/reports/closed-order-ledger-reconciliation?*';
const exportApi='**/reports/closed-order-ledger-reconciliation/export?*';

function payload(fromDay:string,toDay:string,empty=false){
  const kinds=['ADVANCE','REFUND_ADVANCE','APPLY_ADVANCE',
    'PAYMENT','APPLY_ADVANCE'] as const;
  const amounts=['900','200','500','1800','200'];
  const events=empty?[]:kinds.map((kind,i)=>({
    eventId:ids[i],accountId:account,revisionId:revision,kind,
    amountMinor:amounts[i],businessDay:fromDay,
  }));
  const row={
    workOrderId:order,closedDay:fromDay,closedEventId:close,
    previewId:preview,previewDigest:'a'.repeat(64),revisionId:revision,
    approvedTotalMinor:'2500',directCostMinor:'2751',syntheticDifferenceMinor:'-251',
    costStatus:'COMPLETE' as const,demoNetMinor:'2500',
    ledgerStatus:empty?'NO_TREASURY_EVENTS' as const:'SETTLED' as const,
    treasurySettledMinor:empty?'0':'2500',
    unappliedAdvanceMinor:'0',treasuryCashNetMinor:empty?'0':'2500',
    treasuryDueMinor:empty?'2500':'0',
    demoMinusTreasuryMinor:empty?'2500':'0',
    treasuryEvents:events,
    costEvidence:{
      labor:[{laborEntryId:labor,status:'TRACED',approvalId:approval,
        rateRevisionId:revision}],
      lots:[{lotId:lot,skuId:sku,status:'TRACED',receiptId:receipt,
        valuationId:valuation,fifoSlices:[]}],
    },
  };
  const summary={
    orderCount:1,costCompleteCount:1,ledgerLinkedCount:empty?0:1,
    ledgerSettledCount:empty?0:1,treasuryEventCount:events.length,
    approvedTotalMinor:'2500',demoNetMinor:'2500',
    treasurySettledMinor:row.treasurySettledMinor,
    unappliedAdvanceMinor:'0',treasuryCashNetMinor:row.treasuryCashNetMinor,
    treasuryDueMinor:row.treasuryDueMinor,
    demoMinusTreasuryMinor:row.demoMinusTreasuryMinor,
  };
  return {
    syntheticOnly:true,realMoney:false,releaseAllowed:false,
    marginAvailable:false,payrollAvailable:false,currency:'RUB',
    basis:'CLOSED_TECHNICAL_TWO_SEPARATE_SIMULATED_SETTLEMENT_LEDGERS',
    fromDay,toDay,timeZone:'Europe/Berlin',summary,items:[row],
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}

test('448: закрытый ЗН, два независимых журнала, затраты, UUID, мобильный и тёмная тема',async({page},testInfo)=>{
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(p.get('fromDay')??'',p.get('toDay')??''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-closed-order-ledgers');
  const root=page.getByLabel('Сверка закрытых ЗН с двумя реестрами (dev)');
  await expect(root.getByRole('heading',{name:'Закрытые ЗН: первичка, условные расчёты и прямые затраты'}))
    .toBeVisible();
  await expect(root).toContainText('ни реальных платежей');
  const button=root.getByRole('button',{name:'Сверить закрытые ЗН и два реестра'});
  await expect(button).toBeDisabled();
  await scope(page);
  await expect(button).toBeEnabled();
  await button.focus();await page.keyboard.press('Enter');
  const result=page.getByLabel('Сверка закрытых заказов');
  await expect(result.getByRole('status')).toContainText('исходных событий: 5');
  const table=result.getByRole('table',{name:'Закрытые ЗН и два журнала'});
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table).toContainText('-2,51 RUB');
  await expect(table).toContainText('Техническая сумма зачтена в тестовой кассе');
  await result.locator('details').first().locator('summary').first().click();
  await expect(result).toContainText(ids[0]);
  await expect(result).toContainText('Зачёт аванса (не приход)');
  await result.getByText('Доказательства материалов и труда').click();
  await expect(result).toContainText(labor);
  await expect(result).toContainText(valuation);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.screenshot({path:testInfo.outputPath('closed-ledgers-390-light.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('closed-ledgers-1280-dark.png'),
    fullPage:true,animations:'disabled'});
});

test('448: техническое закрытие без события кассы не становится реальной оплатой',async({page})=>{
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(p.get('fromDay')??'',p.get('toDay')??'',true))});
  });
  await page.goto('/staff/auto-closed-order-ledgers');await scope(page);
  await page.getByRole('button',{name:'Сверить закрытые ЗН и два реестра'}).click();
  const result=page.getByLabel('Сверка закрытых заказов');
  await expect(result).toContainText('Нет записей в отдельной тестовой кассе');
  await expect(result.getByRole('table')).toContainText('25,00 RUB');
  await expect(result.getByRole('status')).toContainText('условно оплачено полностью: 0');
});

test('448: ложные итоги/повтор UUID, 403/409/422 очищают старую сверку',async({page})=>{
  let mode:'ok'|'bad'|'duplicate'|'denied'|'conflict'|'invalid'='ok';
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    const data=payload(p.get('fromDay')??'',p.get('toDay')??'');
    if(mode==='bad')data.summary.treasurySettledMinor='100';
    if(mode==='duplicate')data.items[0].treasuryEvents[1].eventId=ids[0];
    const status=mode==='denied'?403:mode==='conflict'?409:mode==='invalid'?422:200;
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/staff/auto-closed-order-ledgers');await scope(page);
  const load=page.getByRole('button',{name:'Сверить закрытые ЗН и два реестра'});
  await load.click();await expect(page.getByLabel('Сверка закрытых заказов')).toBeVisible();
  for(const [next,error] of [
    ['bad','Не удалось проверить полную первичку'],
    ['duplicate','Не удалось проверить полную первичку'],
    ['conflict','Конфликт источников'],
    ['invalid','Некорректная дата'],
    ['denied','Доступ запрещён'],
  ] as const){
    mode=next;await load.click();
    await expect(page.getByRole('alert')).toContainText(error);
    await expect(page.getByLabel('Сверка закрытых заказов')).toHaveCount(0);
    mode='ok';await load.click();
    await expect(page.getByLabel('Сверка закрытых заказов')).toBeVisible();
  }
  await page.getByLabel('UUID филиала').fill('44444444-5555-4666-8777-888888888888');
  await expect(page.getByLabel('Сверка закрытых заказов')).toHaveCount(0);
});

test('448: CSV только после повторной авторизации и полного нового PostgreSQL-снимка',async({page})=>{
  let exportStatus:200|403|409|422=200;
  await page.route(api,route=>{
    const p=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(p.get('fromDay')??'',p.get('toDay')??''))});
  });
  await page.route(exportApi,route=>route.fulfill(exportStatus!==200?
    {status:exportStatus,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
    {status:200,contentType:'text/csv; charset=utf-8',
      body:'\ufeffУровень;Событие UUID\r\nTREASURY_EVENT;'+ids[0]+'\r\n'}));
  await page.goto('/staff/auto-closed-order-ledgers');await scope(page);
  const load=page.getByRole('button',{name:'Сверить закрытые ЗН и два реестра'});
  await load.click();
  const result=page.getByLabel('Сверка закрытых заказов');
  const incoming=page.waitForEvent('download');
  await result.getByRole('button',{name:'Скачать сверку с первичными UUID (CSV)'}).click();
  expect((await incoming).suggestedFilename()).toBe('auto-closed-ledger-reconciliation.csv');
  for(const [code,error] of [[409,'Конфликт источников'],
    [422,'Некорректная дата'],[403,'Доступ запрещён']] as const){
    exportStatus=code;
    await result.getByRole('button',{name:'Скачать сверку с первичными UUID (CSV)'}).click();
    await expect(page.getByRole('alert')).toContainText(error);
    await expect(page.getByLabel('Сверка закрытых заказов')).toHaveCount(0);
    exportStatus=200;await load.click();
    await expect(page.getByLabel('Сверка закрытых заказов')).toBeVisible();
  }
});


test('448: подтверждённая FIFO/serial стоимость раскрывает первичные распределения',async({page})=>{
  await page.route(api,route=>{
    const params=new URL(route.request().url()).searchParams;
    const data=payload(params.get('fromDay')??'',params.get('toDay')??'');
    const lotGroup=data.items[0].costEvidence.lots[0];
    const source=lotGroup as typeof lotGroup & {
      fifoSlices:{stockEventId:string;lotMovementId:string;originalIssueId:string;
        receiptId:string;acceptanceId:string;valuationId:string;
        action:'ISSUE'|'RETURN';quantity:number;unitNetMinor:number;
        serialId:string|null;serialApprovalId:string|null;sourceLocationId:string|null}[];
    };
    source.status='FIFO_TRACED';
    source.receiptId=null as unknown as string;
    source.valuationId=null as unknown as string;
    source.fifoSlices=[
      {stockEventId:'12121212-1212-4121-8121-121212121212',
        lotMovementId:'13131313-1313-4131-8131-131313131313',
        originalIssueId:'14141414-1414-4141-8141-141414141414',
        receiptId:receipt,acceptanceId:'15151515-1515-4151-8151-151515151515',
        valuationId:valuation,action:'ISSUE',quantity:1,unitNetMinor:1250,
        serialId:null,serialApprovalId:null,sourceLocationId:null},
    ];
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/staff/auto-closed-order-ledgers');
  await scope(page);
  await page.getByRole('button',{name:'Сверить закрытые ЗН и два реестра'}).click();
  const result=page.getByLabel('Сверка закрытых заказов');
  await expect(result).toBeVisible();
  await result.locator('details').first().locator('summary').first().click();
  await result.getByText('Доказательства материалов и труда').click();
  await expect(result).toContainText('по первичным FIFO/serial-срезам');
  await expect(result).toContainText('12121212-1212-4121-8121-121212121212');
  await expect(result).toContainText(valuation);
  await expect(result).toContainText(receipt);
});
