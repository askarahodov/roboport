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
