import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const base='**/api/v1/dev/auto/organizations/**/demo-finance-report*';
const sample={
  syntheticOnly:true,legalGatePassed:false,releaseAllowed:false,
  financeCurrency:null,demoCurrency:'RUB',timeZone:'Europe/Berlin',
  basis:'INDEPENDENT_LEDGER_EVENT_LOCAL_DAYS',
  items:[{
    localDay:'2030-10-27',chargeEvents:1,chargeMinor:4500,
    creditEvents:1,creditMinor:1200,correctionNetMinor:3300,
    demoPaymentEvents:2,demoPaymentMinor:3000,
    demoRefundEvents:1,demoRefundMinor:500,demoNetMinor:2500,
    sealedPreviewCount:1,
  }],
};

async function fill(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByLabel('Начало периода').fill('2030-10-27');
  await page.getByLabel('Конец периода').fill('2030-10-27');
}

test('manager reads two separate ledgers in 390/1280 and dark theme',async({page})=>{
  const urls:string[]=[];
  await page.route(base,route=>{
    urls.push(route.request().url());
    expect(route.request().method()).toBe('GET');
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(sample)});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-finance-report');
  await expect(page.getByRole('heading',{name:'Сверка условных начислений и имитации расчётов'})).toBeVisible();
  await fill(page);
  await page.getByRole('button',{name:'Сформировать сверку'}).click();
  const table=page.getByRole('table',{name:'Независимые условный и симулированный журналы по дням'});
  await expect(table).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Начисления, без валюты'})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'Сальдо имитации, RUB'})).toBeVisible();
  const row=page.getByRole('row',{name:/2030-10-27/});
  await expect(row).toContainText('33,00');
  await expect(row).toContainText('25,00');
  await expect(row).toContainText('1');
  expect(urls).toHaveLength(1);
  const parsed=new URL(urls[0]);
  expect(parsed.pathname).toBe('/api/v1/dev/auto/organizations/'+org+
    '/locations/'+loc+'/demo-finance-report');
  expect(parsed.searchParams.get('fromDay')).toBe('2030-10-27');
  expect(parsed.searchParams.get('toDay')).toBe('2030-10-27');
  expect(JSON.stringify(sample)).not.toContain('customerId');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('scoped report 403 and changing branch always clear previous summary',async({page})=>{
  let denied=false;
  await page.route(base,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify(sample),
  }));
  await page.goto('/staff/auto-demo-finance-report');
  await fill(page);
  await page.getByRole('button',{name:'Сформировать сверку'}).click();
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('33333333-3333-4333-8333-333333333333');
  await expect(page.getByRole('table')).toHaveCount(0);
  denied=true;
  await page.getByRole('button',{name:'Сформировать сверку'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('table')).toHaveCount(0);
  await page.getByLabel('Конец периода').fill('2030-12-01');
  await expect(page.getByRole('button',{name:'Сформировать сверку'})).toBeDisabled();
});

test('malformed monetary aggregate, mixed currencies, and bad net fail closed',async({page})=>{
  let reply:unknown={...sample,items:[{...sample.items[0],demoNetMinor:2600}]};
  await page.route(base,route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify(reply),
  }));
  await page.goto('/staff/auto-demo-finance-report');
  await fill(page);
  await page.getByRole('button',{name:'Сформировать сверку'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить сверку');
  await expect(page.getByRole('table')).toHaveCount(0);
  reply={...sample,financeCurrency:'RUB'};
  await page.getByRole('button',{name:'Сформировать сверку'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить сверку');
  await expect(page.getByRole('table')).toHaveCount(0);
  reply={...sample,items:[{...sample.items[0],creditMinor:-1,correctionNetMinor:4501}]};
  await page.getByRole('button',{name:'Сформировать сверку'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось получить сверку');
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('closed-order report reconciles source IDs and clears on revoked scope',async({page},testInfo)=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const row={workOrderId:id('a'),closedDay:'2030-10-27',previewId:id('b'),
    previewDigest:'f'.repeat(64),estimateRevisionId:id('c'),postedEventId:id('d'),
    ackEventId:id('e'),closedEventId:id('f'),approvedBaseMinor:2500,approvedExtraMinor:500,
    approvedTotalMinor:3000,simulatedPaidMinor:3500,simulatedRefundedMinor:500,
    cashEvents:[{id:id('1'),kind:'SIM_PAYMENT',amountMinor:3500,revisionId:id('c')},
      {id:id('2'),kind:'SIM_REFUND',amountMinor:500,revisionId:id('c')}]};
  const payload={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_RECONCILED_PREVIEW_AND_CASH',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,cashEventCount:2,approvedBaseMinor:2500,approvedExtraMinor:500,
      approvedTotalMinor:3000,simulatedPaidMinor:3500,simulatedRefundedMinor:500},items:[row]};
  let status=200,invalid=false;
  await page.route('**/reports/closed-orders*',route=>route.fulfill({
    status,contentType:'application/json',body:JSON.stringify(invalid?
      {...payload,summary:{...payload.summary,approvedTotalMinor:4000}}:payload),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-finance-report');await fill(page);
  const button=page.getByRole('button',{name:'Сверить закрытые заказ-наряды'});
  await button.click();
  const table=page.getByRole('table',{name:'Сверка закрытых заказ-нарядов с первичными событиями'});
  await expect(table).toBeVisible();
  await table.getByText('Первичные события',{exact:true}).click();
  await expect(table.getByText('Проведение: '+id('d'))).toBeVisible();
  await expect(table.getByText(/Имитация возврата:/)).toContainText('5,00');
  await page.screenshot({path:testInfo.outputPath('closed-orders-mobile-light.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('closed-orders-desktop-light.png'),fullPage:true,animations:'disabled'});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('closed-orders-desktop-dark.png'),fullPage:true,animations:'disabled'});
  status=403;await button.click();
  await expect(page.getByRole('alert')).toContainText('Доступ к реестру запрещён');
  await expect(table).toHaveCount(0);
  status=200;invalid=true;await button.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось сверить реестр');
  await expect(table).toHaveCount(0);
  invalid=false;await button.click();await expect(table).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(id('3'));
  await expect(table).toHaveCount(0);
});

test('material assessment shows traceability, unknown values and live price denial',async({page},testInfo)=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const group={skuId:id('1'),lotId:id('2'),issuedQuantity:2,returnedQuantity:1,netQuantity:1,
    status:'TRACED',source:{receiptId:id('3'),acceptanceId:id('4'),valuationId:id('5'),unitNetMinor:1250},
    materialNetMinor:1250,fifoSlices:[],movements:[
      {stockEventId:id('6'),action:'ISSUE',quantity:2,lotMovementId:id('7')},
      {stockEventId:id('8'),action:'RETURN',quantity:1,lotMovementId:id('9')},
    ]};
  const row={workOrderId:id('a'),closedDay:'2030-10-27',closedEventId:id('b'),
    status:'COMPLETE',materialNetMinor:1250,groups:[group]};
  const sample={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,completeOrderCount:1,movementCount:2,materialNetMinor:1250},items:[row]};
  const unknown={...sample,summary:{...sample.summary,completeOrderCount:0,materialNetMinor:null},
    items:[{...row,status:'INCOMPLETE',materialNetMinor:null,
      groups:[{...group,status:'UNPROVEN',source:null,materialNetMinor:null}]}]};
  let response:unknown=sample,status=200;
  await page.route('**/reports/closed-order-materials*',route=>route.fulfill({
    status,contentType:'application/json',body:JSON.stringify(response),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-finance-report');await fill(page);
  const section=page.getByRole('region',{name:'Оценка расхода материалов закрытых заказов'});
  const button=section.getByRole('button',{name:'Проверить расход материалов'});
  const table=section.getByRole('table');
  await button.click();await expect(table).toBeVisible();
  await expect(table).toContainText('12,50 RUB');
  await table.getByText('Партии и движения',{exact:true}).click();
  await expect(table.getByText(/Подтверждён единственный приход/)).toContainText('Расход: 1');
  await expect(table.getByText(/Приход:/)).toContainText(id('3'));
  await page.screenshot({path:testInfo.outputPath('material-assessment-mobile-light.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('material-assessment-desktop-light.png'),fullPage:true,animations:'disabled'});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('material-assessment-desktop-dark.png'),fullPage:true,animations:'disabled'});
  response=unknown;await button.click();
  await expect(table).toContainText('Не определена');
  await expect(table).not.toContainText('12,50 RUB');
  response={...unknown,summary:{...unknown.summary,materialNetMinor:0}};
  await button.click();await expect(section.getByRole('alert')).toContainText('Не удалось проверить');
  await expect(table).toHaveCount(0);
  response=sample;await button.click();await expect(table).toBeVisible();
  status=403;await button.click();
  await expect(section.getByRole('alert')).toContainText('Доступ к закупочным сведениям запрещён');
  await expect(table).toHaveCount(0);
  status=200;await button.click();await expect(table).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(id('c'));
  await expect(table).toHaveCount(0);
});

test('internal cost rate approval and reconciled negative difference, unknowns and live denial',async({page},testInfo)=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const group={skuId:id('1'),lotId:id('2'),issuedQuantity:2,returnedQuantity:1,netQuantity:1,
    status:'TRACED',source:{receiptId:id('3'),acceptanceId:id('4'),valuationId:id('5'),unitNetMinor:1250},
    materialNetMinor:1250,fifoSlices:[],movements:[
      {stockEventId:id('6'),action:'ISSUE',quantity:2,lotMovementId:id('7')},
      {stockEventId:id('8'),action:'RETURN',quantity:1,lotMovementId:id('9')},
    ]};
  const rate={id:id('a'),version:1,rateMinorPerHour:2001,proposedBy:id('b'),
    createdAt:'2030-10-27T08:00:00Z',approvalId:null,approvedBy:null,approvedAt:null};
  const approval={...rate,approvalId:id('c'),approvedBy:id('d'),approvedAt:'2030-10-27T08:01:00Z'};
  const labor={laborEntryId:id('e'),serviceId:id('f'),actualMinutes:45,status:'TRACED',costMinor:1501,
    source:{approvalId:approval.approvalId,revisionId:approval.id,version:1,rateMinorPerHour:2001}};
  const row={workOrderId:id('a'),closedDay:'2030-10-27',closedEventId:id('b'),
    previewId:id('c'),previewDigest:'f'.repeat(64),status:'COMPLETE',approvedTotalMinor:2500,
    materialNetMinor:1250,laborCostMinor:1501,directCostMinor:2751,syntheticDifferenceMinor:-251,
    laborEntries:[labor],materialGroups:[group]};
  const payload={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_TRACED_MATERIAL_AND_APPROVED_INTERNAL_LABOR_COST',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,completeOrderCount:1,laborEntryCount:1,approvedTotalMinor:2500,
      materialNetMinor:1250,laborCostMinor:1501,directCostMinor:2751,syntheticDifferenceMinor:-251},items:[row]};
  const noRate={...payload,summary:{...payload.summary,completeOrderCount:0,laborCostMinor:null,
    directCostMinor:null,syntheticDifferenceMinor:null},items:[{...row,status:'INCOMPLETE',
    laborCostMinor:null,directCostMinor:null,syntheticDifferenceMinor:null,
    laborEntries:[{...labor,status:'NO_APPROVED_RATE',costMinor:null,source:null}]}]};
  let policies={syntheticOnly:true,payrollAvailable:false,currency:'RUB',items:[] as (typeof rate|typeof approval)[],
    active:null as typeof approval|null};
  let report:unknown=payload,status=200;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({csrfToken:'synthetic-csrf'}),
  }));
  await page.route('**/internal-labor-cost-policies*',route=>{
    const req=route.request();
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(req.url().endsWith('/approve')) policies={...policies,items:[approval],active:approval};
      else{
        expect(body.rateMinorPerHour).toBe(2001);
        expect(body.expectedVersion).toBe(0);
        policies={...policies,items:[rate]};
      }
    }
    return route.fulfill({status:req.method()==='POST'?201:200,contentType:'application/json',
      body:JSON.stringify(policies)});
  });
  // Approve has a slash after the path, so use an explicit pattern.
  await page.route('**/internal-labor-cost-policies/*/approve',route=>{
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
    expect(route.request().postDataJSON().requestKey).toMatch(/^[0-9a-f-]{36}$/);
    policies={...policies,items:[approval],active:approval};
    return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify(policies)});
  });
  await page.route('**/reports/internal-order-costs*',route=>route.fulfill({
    status,contentType:'application/json',body:JSON.stringify(report),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-finance-report');await fill(page);
  const section=page.getByRole('region',{name:'Внутренние затраты закрытых заказов'});
  await section.getByRole('button',{name:'Прочитать внутренние ставки'}).click();
  await expect(section.getByText('Активная ставка: Не утверждена.')).toBeVisible();
  await section.getByLabel('Внутренняя ставка, копеек за час').fill('2001');
  await section.getByRole('button',{name:'Предложить внутреннюю ставку'}).click();
  await expect(section.getByText(/Ожидает независимого утверждения/)).toBeVisible();
  await section.getByRole('button',{name:'Утвердить внутреннюю ставку версии 1'}).click();
  await expect(section.getByText(/Активная ставка: 20,01 RUB/)).toBeVisible();
  const calculate=section.getByRole('button',{name:'Рассчитать внутренние затраты'});
  await calculate.click();const table=section.getByRole('table');
  await expect(table).toContainText('-2,51 RUB');
  await table.getByText('Источники затрат',{exact:true}).click();
  await expect(table.getByText(/Труд:/)).toContainText('15,01 RUB');
  await expect(table.getByText(/Приход:/)).toContainText(id('3'));
  await page.screenshot({path:testInfo.outputPath('internal-costs-mobile-light.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('internal-costs-desktop-light.png'),fullPage:true,animations:'disabled'});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('internal-costs-desktop-dark.png'),fullPage:true,animations:'disabled'});
  report=noRate;await calculate.click();await expect(table).toContainText('Не определено');
  report={...noRate,summary:{...noRate.summary,syntheticDifferenceMinor:0}};
  await calculate.click();await expect(section.getByRole('alert')).toContainText('Не удалось проверить');
  await expect(table).toHaveCount(0);
  report=payload;await calculate.click();await expect(table).toBeVisible();
  status=403;await calculate.click();await expect(section.getByRole('alert')).toContainText('Доступ');
  await expect(table).toHaveCount(0);
  status=200;await calculate.click();await expect(table).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(id('f'));
  await expect(table).toHaveCount(0);
});

test('FIFO mixed receipt UI reconciles return slices and rejects invented cost',async({page},testInfo)=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const a={receiptId:id('1'),acceptanceId:id('2'),valuationId:id('3'),unitNetMinor:1250};
  const b={receiptId:id('4'),acceptanceId:id('5'),valuationId:id('6'),unitNetMinor:2000};
  const issue=id('7'),returned=id('8'),issueEvent=id('9'),returnEvent=id('a');
  const slices=[
    {...a,stockEventId:issueEvent,lotMovementId:issue,originalIssueId:issue,action:'ISSUE',quantity:3},
    {...b,stockEventId:issueEvent,lotMovementId:issue,originalIssueId:issue,action:'ISSUE',quantity:2},
    {...b,stockEventId:returnEvent,lotMovementId:returned,originalIssueId:issue,action:'RETURN',quantity:1},
  ];
  const group={skuId:id('b'),lotId:id('c'),issuedQuantity:5,returnedQuantity:1,netQuantity:4,
    status:'FIFO_TRACED',source:null,materialNetMinor:5750,fifoSlices:slices,movements:[
      {stockEventId:issueEvent,action:'ISSUE',quantity:5,lotMovementId:issue},
      {stockEventId:returnEvent,action:'RETURN',quantity:1,lotMovementId:returned},
    ]};
  const row={workOrderId:id('d'),closedDay:'2030-10-27',closedEventId:id('e'),
    status:'COMPLETE',materialNetMinor:5750,groups:[group]};
  const payload={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,completeOrderCount:1,movementCount:2,materialNetMinor:5750},items:[row]};
  let response:unknown=payload;
  await page.route('**/reports/closed-order-materials*',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify(response),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-finance-report');await fill(page);
  const section=page.getByRole('region',{name:'Оценка расхода материалов закрытых заказов'});
  const button=section.getByRole('button',{name:'Проверить расход материалов'});
  const table=section.getByRole('table');
  await button.click();await expect(table).toContainText('57,50 RUB');
  await table.getByText('Партии и движения',{exact:true}).click();
  await expect(table.getByText(/FIFO: Возврат/)).toContainText('20,00 RUB');
  await expect(table.getByText(/FIFO: Возврат/)).toContainText(issue);
  await page.screenshot({path:testInfo.outputPath('fifo-mobile-light.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('fifo-desktop-light.png'),fullPage:true,animations:'disabled'});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('fifo-desktop-dark.png'),fullPage:true,animations:'disabled'});
  response={...payload,items:[{...row,groups:[{...group,fifoSlices:slices.slice(0,2)}]}]};
  await button.click();await expect(section.getByRole('alert')).toContainText('Не удалось проверить');
  await expect(table).toHaveCount(0);
  response={...payload,items:[{...row,groups:[{...group,fifoSlices:[...slices.slice(0,2),
    {...slices[2],originalIssueId:id('f')}]}]}]};
  await button.click();await expect(section.getByRole('alert')).toContainText('Не удалось проверить');
  await expect(table).toHaveCount(0);
});

test('approved multi-receipt serial costs have an explained reversible source',async({page},testInfo)=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const issue1=id('1'),issue2=id('2'),returned=id('3');
  const approval1=id('4'),approval2=id('5');
  const serial1=id('6'),serial2=id('7');
  const receipt1=id('8'),receipt2=id('9');
  const a={receiptId:receipt1,acceptanceId:id('a'),valuationId:id('b'),
    unitNetMinor:1250,serialId:serial1,serialApprovalId:approval1};
  const b={receiptId:receipt2,acceptanceId:id('c'),valuationId:id('d'),
    unitNetMinor:2000,serialId:serial2,serialApprovalId:approval2};
  const movement1=id('e'),movement2=id('f'),movement3=id('0');
  const group={skuId:id('a'),lotId:id('b'),issuedQuantity:2,returnedQuantity:1,netQuantity:1,
    status:'SERIAL_TRACED',source:null,materialNetMinor:1250,
    fifoSlices:[
      {...a,stockEventId:movement1,lotMovementId:issue1,originalIssueId:issue1,action:'ISSUE',quantity:1},
      {...b,stockEventId:movement2,lotMovementId:issue2,originalIssueId:issue2,action:'ISSUE',quantity:1},
      {...b,stockEventId:movement3,lotMovementId:returned,originalIssueId:issue2,action:'RETURN',quantity:1},
    ],
    movements:[
      {stockEventId:movement1,action:'ISSUE',quantity:1,lotMovementId:issue1},
      {stockEventId:movement2,action:'ISSUE',quantity:1,lotMovementId:issue2},
      {stockEventId:movement3,action:'RETURN',quantity:1,lotMovementId:returned},
    ]};
  const item={workOrderId:id('a'),closedDay:'2030-10-27',closedEventId:id('b'),
    status:'COMPLETE',materialNetMinor:1250,groups:[group]};
  const payload={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,completeOrderCount:1,movementCount:3,materialNetMinor:1250},
    items:[item]};
  let result:unknown=payload;
  await page.route('**/reports/closed-order-materials*',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify(result),
  }));
  await page.setViewportSize({width:390,height:844});await page.goto('/staff/auto-demo-finance-report');
  await fill(page);
  const section=page.getByRole('region',{name:'Оценка расхода материалов закрытых заказов'});
  const check=section.getByRole('button',{name:'Проверить расход материалов'});
  await check.click();
  const table=section.getByRole('table');await expect(table).toContainText('12,50 RUB');
  await table.getByText('Партии и движения',{exact:true}).click();
  await expect(table.getByText(/Серийная деталь: Возврат/)).toContainText(approval2);
  await expect(table.getByText(/Серийная деталь: Возврат/)).toContainText(issue2);
  await page.screenshot({path:testInfo.outputPath('serial-cost-mobile-light.png'),
    animations:'disabled',fullPage:true});
  await page.setViewportSize({width:1280,height:800});
  await page.screenshot({path:testInfo.outputPath('serial-cost-desktop-light.png'),
    animations:'disabled',fullPage:true});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('serial-cost-desktop-dark.png'),
    animations:'disabled',fullPage:true});
  result={...payload,items:[{...item,groups:[{...group,fifoSlices:[
    ...group.fifoSlices.slice(0,2),{...group.fifoSlices[2],serialApprovalId:id('9')},
  ]}]}]};
  await check.click();
  await expect(section.getByRole('alert')).toContainText('Не удалось проверить расход');
  await expect(table).toHaveCount(0);
});


test('interbranch serial cost shows source branch and rejects tampered provenance',async({page},testInfo)=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const source=id('a'),issue1=id('1'),issue2=id('2'),ret=id('3');
  const serial1=id('4'),serial2=id('5');
  const receipt1=id('6'),receipt2=id('7');
  const common=(serialId:string,receiptId:string,unitNetMinor:number,origin:string)=>({
    serialId,receiptId,unitNetMinor,originalIssueId:origin,sourceLocationId:source,
    serialApprovalId:id(serialId===serial1?'8':'9'),
    acceptanceId:id(serialId===serial1?'b':'c'),
    valuationId:id(serialId===serial1?'d':'e'),
  });
  const s1={...common(serial1,receipt1,1250,issue1),
    stockEventId:id('f'),lotMovementId:issue1,action:'ISSUE',quantity:1};
  const s2={...common(serial2,receipt2,2000,issue2),
    stockEventId:id('a'),lotMovementId:issue2,action:'ISSUE',quantity:1};
  const sr={...common(serial2,receipt2,2000,issue2),
    stockEventId:id('b'),lotMovementId:ret,action:'RETURN',quantity:1};
  const group={skuId:id('c'),lotId:id('d'),issuedQuantity:2,returnedQuantity:1,netQuantity:1,
    status:'SERIAL_TRACED',source:null,materialNetMinor:1250,
    fifoSlices:[s1,s2,sr],movements:[
      {stockEventId:s1.stockEventId,lotMovementId:issue1,action:'ISSUE',quantity:1},
      {stockEventId:s2.stockEventId,lotMovementId:issue2,action:'ISSUE',quantity:1},
      {stockEventId:sr.stockEventId,lotMovementId:ret,action:'RETURN',quantity:1},
    ]};
  const row={workOrderId:id('e'),closedDay:'2030-10-27',closedEventId:id('f'),
    status:'COMPLETE',materialNetMinor:1250,groups:[group]};
  const payload={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,completeOrderCount:1,movementCount:3,materialNetMinor:1250},items:[row]};
  let response:unknown=payload,status=200;
  await page.route('**/reports/closed-order-materials*',route=>route.fulfill({
    status,contentType:'application/json',body:JSON.stringify(response),
  }));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-finance-report');await fill(page);
  const section=page.getByRole('region',{name:'Оценка расхода материалов закрытых заказов'});
  const button=section.getByRole('button',{name:'Проверить расход материалов'});
  const table=section.getByRole('table');
  await button.click();
  await expect(table).toContainText('12,50 RUB');
  await table.getByText('Партии и движения',{exact:true}).click();
  await expect(table.getByText(/Филиал закупки:/).first()).toContainText(source);
  await page.screenshot({path:testInfo.outputPath('serial-interbranch-mobile-light.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.screenshot({path:testInfo.outputPath('serial-interbranch-desktop-light.png'),
    fullPage:true,animations:'disabled'});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('serial-interbranch-desktop-dark.png'),
    fullPage:true,animations:'disabled'});
  response={...payload,items:[{...row,groups:[{...group,
    fifoSlices:[s1,{...s2,sourceLocationId:'not-a-uuid'},sr]}]}]};
  await button.click();
  await expect(section.getByRole('alert')).toContainText('Не удалось проверить');
  await expect(table).toHaveCount(0);
  response=payload;await button.click();await expect(table).toBeVisible();
  status=403;await button.click();
  await expect(section.getByRole('alert')).toContainText('Доступ');
  await expect(table).toHaveCount(0);
});


test('closed-order CSV denial clears previously loaded source evidence and restores only on fresh read',async({page})=>{
  const id=(n:string)=>n.repeat(8)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(4)+'-'+n.repeat(12);
  const row={workOrderId:id('a'),closedDay:'2030-10-27',previewId:id('b'),
    previewDigest:'f'.repeat(64),estimateRevisionId:id('c'),postedEventId:id('d'),
    ackEventId:id('e'),closedEventId:id('f'),approvedBaseMinor:2500,approvedExtraMinor:0,
    approvedTotalMinor:2500,simulatedPaidMinor:2500,simulatedRefundedMinor:0,
    cashEvents:[{id:id('1'),kind:'SIM_PAYMENT',amountMinor:2500,revisionId:id('c')}]};
  const data={syntheticOnly:true,realMoney:false,releaseAllowed:false,marginAvailable:false,
    payrollAvailable:false,currency:'RUB',basis:'CLOSED_TECHNICAL_RECONCILED_PREVIEW_AND_CASH',
    timeZone:'Europe/Berlin',fromDay:'2030-10-27',toDay:'2030-10-27',
    summary:{orderCount:1,cashEventCount:1,approvedBaseMinor:2500,approvedExtraMinor:0,
      approvedTotalMinor:2500,simulatedPaidMinor:2500,simulatedRefundedMinor:0},items:[row]};
  let denied=false,downloads=0;
  page.on('download',()=>downloads++);
  await page.route('**/reports/closed-orders**',route=>{
    const path=new URL(route.request().url()).pathname;
    const reject=denied||path.endsWith('/export');
    return route.fulfill({status:reject?403:200,contentType:'application/json',
      body:reject?'{"code":"FORBIDDEN"}':JSON.stringify(data)});
  });
  await page.goto('/staff/auto-demo-finance-report');
  await fill(page);
  const load=page.getByRole('button',{name:'Сверить закрытые заказ-наряды'});
  const table=page.getByRole('table',{name:'Сверка закрытых заказ-нарядов с первичными событиями'});
  await load.click();
  await expect(table).toBeVisible();
  await page.getByRole('button',{name:'Скачать реестр CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ к реестру запрещён');
  await expect(table).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Скачать реестр CSV'})).toHaveCount(0);
  denied=true;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ к реестру запрещён');
  denied=false;
  await load.click();
  await expect(table).toBeVisible();
  expect(downloads).toBe(0);
});
