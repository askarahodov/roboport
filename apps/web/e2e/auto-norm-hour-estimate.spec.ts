import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const service='44444444-4444-4444-8444-444444444444';
const revisionId='55555555-5555-4555-8555-555555555555';
const query=new URLSearchParams({org,loc,order}).toString();

test('manager publishes rated norm-hours; client approves immutable rate breakdown',async({page})=>{
  let quote=false,approved=false;
  const actions:string[]=[];
  const ratedLine={serviceId:service,code:'SYN-LABOR',title:'Синтетическая работа',
    durationMinutes:45,pricingMethod:'NORM_HOUR',qualificationCode:'DIAGNOSTIC',
    rateMinorPerHour:10001,amountMinor:7501};
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const payload=req.postDataJSON();
      expect(payload.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(pathname.endsWith('/estimates/decision')){
        expect(payload).toMatchObject({revisionId,decision:'APPROVED'});
        approved=true;actions.push('APPROVED');
      }else if(pathname.endsWith('/estimates')){
        expect(payload).toEqual({
          requestKey:expect.any(String),expectedVersion:0,
          laborRates:[{serviceId:service,qualificationCode:'DIAGNOSTIC',
            rateMinorPerHour:10001}],
        });
        expect(payload.prices).toBeUndefined();
        quote=true;actions.push('PUBLISHED');
      }else throw Error('Unexpected route '+pathname);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"replayed":false}'});
    }
    if(pathname.endsWith('/services'))return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({items:[{id:service,code:'SYN-LABOR',
        title:'Синтетическая работа',durationMinutes:45}]})});
    if(pathname.endsWith('/estimates'))return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
        latestVersion:quote?1:0,
        items:quote?[{id:revisionId,version:1,totalMinor:7501,
          currency:'RUB',digest:'a'.repeat(64),
          lines:[ratedLine],diagnostics:[],
          decision:approved?'APPROVED':null,createdAt:'2030-03-01T10:00:00Z',
          decidedAt:null}]:[]})});
    throw Error('Unexpected request '+pathname);
  });

  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-estimates?'+query);
  await expect(page.getByRole('heading',{name:'Сметы заказ-наряда (dev)'})).toBeVisible();
  await page.getByRole('combobox',{name:'Способ расчёта стоимости'})
    .selectOption('NORM_HOUR');
  await expect(page.getByRole('button',{name:'Опубликовать версию'})).toBeDisabled();
  await page.getByRole('combobox',{name:'Категория тарифа SYN-LABOR'})
    .selectOption('DIAGNOSTIC');
  await page.getByRole('spinbutton',{name:'Ставка нормо-часа SYN-LABOR'}).fill('10001');
  await expect(page.getByText('Расчёт SYN-LABOR: 7501 коп.')).toBeVisible();
  await page.getByRole('button',{name:'Опубликовать версию'}).click();
  await expect(page.getByRole('heading',{name:'Версия 1: 7501 коп. (RUB)'}))
    .toBeVisible();
  await expect(page.getByText('Расчёт по нормо-часу:',{exact:false})).toContainText(
    '10001 коп./час');
  await page.goto('/account/auto-estimates?'+query);
  await expect(page.getByRole('heading',{name:'Версия 1: 7501 коп. (RUB)'}))
    .toBeVisible();
  await expect(page.getByText('Расчёт по нормо-часу:',{exact:false})).toContainText(
    'DIAGNOSTIC');
  await page.getByRole('button',{name:'Согласовать версию 1'}).click();
  await expect(page.getByText('Решение: APPROVED')).toBeVisible();
  expect(actions).toEqual(['PUBLISHED','APPROVED']);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
});

test('client norm-hour price and approved tariff vanish after scope revocation',async({page})=>{
  let revoked=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>
    route.fulfill(revoked?{status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'}:{status:200,contentType:'application/json',
      body:JSON.stringify({items:[{id:revisionId,version:1,totalMinor:7501,
        currency:'RUB',digest:'b'.repeat(64),decision:'APPROVED',
        createdAt:'2030-03-01T10:00:00Z',decidedAt:null,diagnostics:[],
        lines:[{serviceId:service,code:'PRIVATE-RATE',title:'PRIVATE',
          durationMinutes:45,pricingMethod:'NORM_HOUR',
          qualificationCode:'MASTER',rateMinorPerHour:10001,amountMinor:7501}]}],
        latestVersion:1,syntheticOnly:true,releaseAllowed:false})}));
  await page.goto('/account/auto-estimates?'+query);
  await expect(page.getByText('PRIVATE-RATE',{exact:false})).toBeVisible();
  revoked=true;
  await page.getByRole('button',{name:'Обновить версии'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('PRIVATE-RATE',{exact:false})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Версия 1: 7501 коп. (RUB)'}))
    .toHaveCount(0);
});
