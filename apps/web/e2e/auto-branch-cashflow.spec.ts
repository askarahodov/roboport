import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const cash='33333333-3333-4333-8333-333333333333';
const bank='44444444-4444-4444-8444-444444444444';
const e1='55555555-5555-4555-8555-555555555555';
const e2='66666666-6666-4666-8666-666666666666';
const e3='77777777-7777-4777-8777-777777777777';
const e4='88888888-8888-4888-8888-888888888888';
const e5='99999999-9999-4999-8999-999999999999';
const closeA='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const closeB='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const api='**/treasury/cashflow?*';
const exportApi='**/treasury/cashflow/export?*';

const daySequence=(from:string,to:string)=>Array.from({
  length:(Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000+1,
},(_,i)=>new Date(Date.parse(from+'T00:00:00Z')+i*86400000).toISOString().slice(0,10));

function payload(fromDay:string,toDay:string){
  const days=daySequence(fromDay,toDay);
  const first=days[0];
  const stamp=first+'T09:00:00+00:00';
  const accounts=[
    {accountId:cash,accountCode:'CASH-01',accountKind:'CASH_SIM',
      values:{in:900,out:200,internal:400,close:700},
      events:[
        {eventId:e1,kind:'ADVANCE',cashFlow:'IN',amountMinor:'900',createdAt:stamp},
        {eventId:e2,kind:'REFUND_ADVANCE',cashFlow:'OUT',amountMinor:'200',createdAt:stamp},
        {eventId:e3,kind:'APPLY_ADVANCE',cashFlow:'INTERNAL',amountMinor:'400',createdAt:stamp},
      ],closeId:closeA},
    {accountId:bank,accountCode:'BANK-01',accountKind:'BANK_SIM',
      values:{in:1000,out:100,internal:0,close:900},
      events:[
        {eventId:e4,kind:'PAYMENT',cashFlow:'IN',amountMinor:'1000',createdAt:stamp},
        {eventId:e5,kind:'REFUND_PAYMENT',cashFlow:'OUT',amountMinor:'100',createdAt:stamp},
      ],closeId:closeB},
  ];
  const detailed=accounts.map(a=>({
    accountId:a.accountId,accountCode:a.accountCode,accountKind:a.accountKind,
    summary:{openingMinor:'0',incomingMinor:String(a.values.in),
      outgoingMinor:String(a.values.out),internalAppliedMinor:String(a.values.internal),
      closingMinor:String(a.values.close),eventCount:a.events.length,sealedDayCount:1},
    days:days.map((localDay,i)=>({
      localDay,openingMinor:String(i===0?0:a.values.close),
      incomingMinor:String(i===0?a.values.in:0),
      outgoingMinor:String(i===0?a.values.out:0),
      internalAppliedMinor:String(i===0?a.values.internal:0),
      closingMinor:String(a.values.close),
      closeId:i===0?a.closeId:null,
      events:i===0?a.events:[],
    })),
  }));
  return {
    syntheticOnly:true,realMoney:false,releaseAllowed:false,currency:'RUB',
    basis:'VERIFIED_BRANCH_CASHFLOW_FROM_IMMUTABLE_ACCOUNT_CASHBOOKS',
    fromDay,toDay,maxAccounts:50,maxEvents:1000,
    summary:{accountCount:2,eventCount:5,sealedAccountDayCount:2,
      openingMinor:'0',incomingMinor:'1900',outgoingMinor:'300',
      internalAppliedMinor:'400',closingMinor:'1600'},
    days:days.map((localDay,i)=>({
      localDay,openingMinor:String(i===0?0:1600),
      incomingMinor:String(i===0?1900:0),
      outgoingMinor:String(i===0?300:0),
      internalAppliedMinor:String(i===0?400:0),
      closingMinor:'1600',eventCount:i===0?5:0,sealedAccountCount:i===0?2:0,
    })),
    accounts:detailed,
  };
}
async function scope(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
}

test('445: ДДС филиала, 2 кассы, источники, зачёт без движения, 390/1280 и keyboard',async({page},info)=>{
  await page.route(api,route=>{
    const params=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(params.get('fromDay')??'',params.get('toDay')??''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-branch-cashflow');
  const root=page.getByLabel('ДДС филиала по первичным событиям (dev)');
  await expect(root.getByRole('heading',{name:'ДДС филиала: проверяемая синтетическая сверка'}))
    .toBeVisible();
  await expect(root).toContainText('не реальные деньги');
  const button=root.getByRole('button',{name:'Сверить ДДС филиала'});
  await expect(button).toBeDisabled();
  await scope(page);await expect(button).toBeEnabled();
  await button.focus();await page.keyboard.press('Enter');
  const report=page.getByLabel('Результаты ДДС филиала');
  await expect(report.getByRole('status')).toContainText('событий: 5');
  await expect(report.getByRole('status')).toContainText('зачтено без движения: 4,00 RUB');
  await expect(report.getByRole('table',{name:'Движение по дням филиала'})
    .locator('tbody tr')).toHaveCount(1);
  const account=report.locator('details').filter({has:page.locator('summary',{hasText:'CASH-01'})}).first();
  await account.locator('summary').first().click();
  await account.locator('details summary').first().click();
  await expect(account).toContainText(e1);
  await expect(account).toContainText(e3);
  await expect(account).toContainText('Зачёт аванса без движения');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('branch-cashflow-390-light.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:info.outputPath('branch-cashflow-1280-dark.png'),
    fullPage:true,animations:'disabled'});
});

test('445: испорченные суммы/повтор источника, 403/409/422 очищают предыдущий отчёт',async({page})=>{
  let mode:'ok'|'tamper'|'duplicate'|'denied'|'limit'|'invalid'='ok';
  await page.route(api,route=>{
    const params=new URL(route.request().url()).searchParams;
    const data=payload(params.get('fromDay')??'',params.get('toDay')??'');
    if(mode==='tamper')data.summary.closingMinor='1700';
    if(mode==='duplicate')data.accounts[1].days[0].events[0].eventId=e1;
    const status=mode==='denied'?403:mode==='limit'?409:mode==='invalid'?422:200;
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/staff/auto-branch-cashflow');await scope(page);
  const load=page.getByRole('button',{name:'Сверить ДДС филиала'});
  await load.click();
  await expect(page.getByLabel('Результаты ДДС филиала')).toBeVisible();
  for(const [next,error] of [
    ['tamper','Не удалось проверить'],
    ['duplicate','Не удалось проверить'],
    ['limit','50 счетов / 1000 событий'],
    ['invalid','Неверный период'],
    ['denied','Доступ запрещён'],
  ] as const){
    mode=next;await load.click();
    await expect(page.getByRole('alert')).toContainText(error);
    await expect(page.getByLabel('Результаты ДДС филиала')).toHaveCount(0);
    mode='ok';await load.click();
    await expect(page.getByLabel('Результаты ДДС филиала')).toBeVisible();
  }
  await page.getByLabel('UUID филиала').fill('cccccccc-cccc-4ccc-8ccc-cccccccccccc');
  await expect(page.getByLabel('Результаты ДДС филиала')).toHaveCount(0);
});

test('445: CSV выгружается только после новой авторизации; 409/403 очищают экран',async({page})=>{
  let status:200|403|409=200;
  await page.route(api,route=>{
    const params=new URL(route.request().url()).searchParams;
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(params.get('fromDay')??'',params.get('toDay')??''))});
  });
  await page.route(exportApi,route=>route.fulfill(status!==200?
    {status,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
    {status:200,contentType:'text/csv; charset=utf-8',
      body:'\ufeffЛокальный день;Событие UUID\r\n2026-11-19;'+e1+'\r\n'}));
  await page.goto('/staff/auto-branch-cashflow');await scope(page);
  const load=page.getByRole('button',{name:'Сверить ДДС филиала'});
  await load.click();
  const report=page.getByLabel('Результаты ДДС филиала');
  const download=page.waitForEvent('download');
  await report.getByRole('button',{name:'Скачать полный ДДС с UUID первички (CSV)'}).click();
  expect((await download).suggestedFilename()).toBe('auto-branch-cashflow.csv');
  for(const [code,text] of [[409,'Конфликт'],[403,'Доступ запрещён']] as const){
    status=code;
    await report.getByRole('button',{name:'Скачать полный ДДС с UUID первички (CSV)'}).click();
    await expect(page.getByRole('alert')).toContainText(text);
    await expect(page.getByLabel('Результаты ДДС филиала')).toHaveCount(0);
    status=200;await load.click();
    await expect(page.getByLabel('Результаты ДДС филиала')).toBeVisible();
  }
});
