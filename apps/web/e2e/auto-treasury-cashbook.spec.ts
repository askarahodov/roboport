import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const account='33333333-3333-4333-8333-333333333333';
const event1='44444444-4444-4444-8444-444444444444';
const event2='55555555-5555-4555-8555-555555555555';
const event3='66666666-6666-4666-8666-666666666666';
const close='77777777-7777-4777-8777-777777777777';
const prefix='**/treasury/accounts/**/cashbook?*';
const exportUrl='**/treasury/accounts/**/cashbook/export?*';

function payload(fromDay:string,toDay:string){
  const days=(Date.parse(toDay+'T00:00:00Z')-Date.parse(fromDay+'T00:00:00Z'))/86400000+1;
  return {
    syntheticOnly:true,realMoney:false,releaseAllowed:false,currency:'RUB',
    basis:'IMMUTABLE_TREASURY_ACCOUNT_CASHBOOK_DAYS',
    accountId:account,accountCode:'CASH-01',accountKind:'CASH_SIM',
    fromDay,toDay,
    summary:{openingMinor:'0',incomingMinor:'1500',outgoingMinor:'200',
      internalAppliedMinor:'400',closingMinor:'1300',eventCount:3,sealedDayCount:1},
    items:Array.from({length:days},(_,i)=>{
      const localDay=new Date(Date.parse(fromDay+'T00:00:00Z')+i*86400000)
        .toISOString().slice(0,10);
      const first=i===0;
      return {localDay,openingMinor:first?'0':'1300',
        incomingMinor:first?'1500':'0',outgoingMinor:first?'200':'0',
        internalAppliedMinor:first?'400':'0',closingMinor:'1300',
        closeId:first?close:null,
        events:first?[
          {eventId:event1,kind:'ADVANCE',amountMinor:'1500',cashFlow:'IN',
            createdAt:'2026-11-11T08:00:00+00:00'},
          {eventId:event2,kind:'REFUND_ADVANCE',amountMinor:'200',cashFlow:'OUT',
            createdAt:'2026-11-11T09:00:00+00:00'},
          {eventId:event3,kind:'APPLY_ADVANCE',amountMinor:'400',cashFlow:'INTERNAL',
            createdAt:'2026-11-11T10:00:00+00:00'},
        ]:[],
      };
    }),
  };
}
async function fill(page:import('@playwright/test').Page){
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByLabel('UUID тестового счёта').fill(account);
}

test('кассовая книга: сверка входящего, расхода и зачёта, 390/1280 и dark',async({page})=>{
  const requests:string[]=[];
  await page.route(prefix,route=>{
    const url=new URL(route.request().url());
    requests.push(url.pathname);
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(url.searchParams.get('fromDay')||'',
                                  url.searchParams.get('toDay')||''))});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-treasury-cashbook');
  const view=page.getByLabel('Синтетическая кассовая книга (dev)');
  await expect(view.getByRole('heading',{
    name:'Кассовая книга: первичные движения и закрытие дня'})).toBeVisible();
  await expect(view).toContainText('не реальные деньги');
  const load=page.getByRole('button',{name:'Сверить кассовую книгу'});
  await expect(load).toBeDisabled();
  await fill(page);
  await expect(load).toBeEnabled();
  await load.focus();
  await page.keyboard.press('Enter');
  const report=page.getByLabel('Результаты кассовой книги');
  await expect(report.getByRole('status')).toContainText('Конечный остаток: 13,00 RUB');
  await expect(report).toContainText('зачёт авансов без движения: 4,00');
  const table=report.getByRole('table',{name:'Сверка тестовых движений по дням'});
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table).toContainText('Закрыто');
  await report.getByText('Событий: 3').click();
  await expect(report).toContainText('Зачёт аванса без движения');
  await expect(report).toContainText(event3);
  expect(requests).toHaveLength(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await page.screenshot({path:'test-results/treasury-cashbook-390-light.png'});
  await page.setViewportSize({width:1280,height:900});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.screenshot({path:'test-results/treasury-cashbook-1280-dark.png'});
});

test('кассовая книга: повреждённые дневные суммы, 409 и отказ доступа',async({page})=>{
  let mode:'ok'|'bad'|'limit'|'denied'='ok';
  await page.route(prefix,route=>{
    const url=new URL(route.request().url());
    const data=payload(url.searchParams.get('fromDay')||'',
                       url.searchParams.get('toDay')||'');
    if(mode==='bad')data.summary.closingMinor='999999';
    return route.fulfill({status:mode==='limit'?409:mode==='denied'?403:200,
      contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/staff/auto-treasury-cashbook');
  await fill(page);
  const load=page.getByRole('button',{name:'Сверить кассовую книгу'});
  await load.click();
  await expect(page.getByLabel('Результаты кассовой книги')).toBeVisible();
  mode='bad';await load.click();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить журнал');
  await expect(page.getByLabel('Результаты кассовой книги')).toHaveCount(0);
  mode='limit';await load.click();
  await expect(page.getByRole('alert')).toContainText('Книга превышает 1000');
  mode='denied';await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await page.getByLabel('UUID филиала').fill('88888888-8888-4888-8888-888888888888');
  await expect(page.getByLabel('Результаты кассовой книги')).toHaveCount(0);
});

test('кассовая книга: CSV скачивание и повторная проверка отозванной роли',async({page})=>{
  let denied=false;
  await page.route(prefix,route=>{
    const u=new URL(route.request().url());
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(u.searchParams.get('fromDay')||'',
                                  u.searchParams.get('toDay')||''))});
  });
  await page.route(exportUrl,route=>route.fulfill({
    status:denied?403:200,
    contentType:denied?'application/json':'text/csv; charset=utf-8',
    body:denied?'{"code":"FORBIDDEN"}':'\ufeffДень;Счёт UUID\r\n2026-11-11;'+account+'\r\n',
  }));
  await page.goto('/staff/auto-treasury-cashbook');
  await fill(page);
  await page.getByRole('button',{name:'Сверить кассовую книгу'}).click();
  const result=page.getByLabel('Результаты кассовой книги');
  await expect(result).toBeVisible();
  const downloading=page.waitForEvent('download');
  await result.getByRole('button',{name:'Скачать полную кассовую книгу CSV'}).click();
  expect((await downloading).suggestedFilename()).toBe('auto-treasury-cashbook.csv');
  denied=true;
  await result.getByRole('button',{name:'Скачать полную кассовую книгу CSV'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(result).toHaveCount(0);
});


test('кассовая книга: новая сверка отменяет незавершённый CSV и восстанавливает кнопку',async({page})=>{
  await page.route(prefix,route=>{
    const u=new URL(route.request().url());
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(payload(u.searchParams.get('fromDay')||'',
                                  u.searchParams.get('toDay')||''))});
  });
  await page.route(exportUrl,async route=>{
    await new Promise(resolve=>setTimeout(resolve,1100));
    try{await route.fulfill({status:200,contentType:'text/csv',
      body:'\ufeffДень;Сумма\r\n'});}catch{/* Original fetch was aborted by a new report. */}
  });
  await page.goto('/staff/auto-treasury-cashbook');
  await fill(page);
  const button=page.getByRole('button',{name:'Сверить кассовую книгу'});
  await button.click();
  const section=page.getByLabel('Результаты кассовой книги');
  const exportButton=section.getByRole('button',{name:'Скачать полную кассовую книгу CSV'});
  await exportButton.click();
  await expect(section.getByRole('button',{name:'Готовим CSV…'})).toBeDisabled();
  await button.click();
  await expect(page.getByLabel('Результаты кассовой книги')).toBeVisible();
  await expect(page.getByRole('button',{name:'Скачать полную кассовую книгу CSV'}))
    .toBeEnabled();
});
