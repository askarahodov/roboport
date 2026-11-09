import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const original='44444444-4444-4444-8444-444444444444';
const additional='66666666-6666-4666-8666-666666666666';
const initialRevision='55555555-5555-4555-8555-555555555555';
const extraRevision='77777777-7777-4777-8777-777777777777';
const query=new URLSearchParams({org,loc,order}).toString();

test('separate additional-work consent at 390 and 1280 pixels',async({page})=>{
  let extraPublished=false,extraApproved=false;
  const events:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const payload=req.postDataJSON();
      expect(payload.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(path.endsWith('/extras/decision')){
        expect(payload).toMatchObject({revisionId:extraRevision,decision:'APPROVED'});
        extraApproved=true;events.push('EXTRA_APPROVED');
      }else if(path.endsWith('/extras')){
        expect(payload).toEqual({baseRevisionId:initialRevision,
          requestKey:expect.any(String),expectedVersion:0,
          workRates:[{serviceId:additional,qualificationCode:'MASTER',
            rateMinorPerHour:10001,quantity:2,
            coefficientBps:12500,discountBps:1000}]});
        extraPublished=true;events.push('EXTRA_PUBLISHED');
      }else throw Error('Unexpected POST '+path);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"replayed":false}'});
    }
    if(path.endsWith('/services'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({items:[{id:original,
        code:'BASE',title:'Исходная работа',durationMinutes:45}]})});
    if(path.endsWith('/service-catalog'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({items:[
        {id:original,code:'BASE',title:'Исходная работа',durationMinutes:45},
        {id:additional,code:'EXTRA',title:'Дополнительная работа',durationMinutes:30},
      ]})});
    if(path.endsWith('/estimates'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({syntheticOnly:true,
        releaseAllowed:false,latestVersion:1,items:[{
          id:initialRevision,version:1,totalMinor:7501,currency:'RUB',
          digest:'a'.repeat(64),decision:'APPROVED',decidedAt:null,
          createdAt:'2030-04-01T12:00:00Z',
          lines:[{serviceId:original,code:'BASE',title:'Исходная работа',
            durationMinutes:45,pricingMethod:'NORM_HOUR',
            qualificationCode:'GENERAL',rateMinorPerHour:10001,
            amountMinor:7501}],diagnostics:[]}]})});
    if(path.endsWith('/extras'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({syntheticOnly:true,
        releaseAllowed:false,latestVersion:extraPublished?1:0,
        items:extraPublished?[{id:extraRevision,version:1,
          baseRevisionId:initialRevision,totalMinor:11251,digest:'b'.repeat(64),
          decision:extraApproved?'APPROVED':null,
          lines:[{serviceId:additional,code:'EXTRA',title:'Дополнительная работа',
            durationMinutes:30,qualificationCode:'MASTER',
            rateMinorPerHour:10001,quantity:2,coefficientBps:12500,
            discountBps:1000,amountMinor:11251}]}]:[]})});
    throw Error('Unexpected GET '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-estimates?'+query);
  await page.getByRole('button',{name:'Открыть дополнительные работы'}).click();
  await expect(page.getByRole('heading',{name:'Новая версия допработ'})).toBeVisible();
  await page.getByRole('checkbox',{name:'Добавить допработу EXTRA'}).check();
  await page.getByRole('combobox',{name:'Категория допработы EXTRA'}).selectOption('MASTER');
  await page.getByRole('spinbutton',{name:'Ставка допработы EXTRA'}).fill('10001');
  await page.getByRole('spinbutton',{name:'Кратность допработы EXTRA'}).fill('2');
  await page.getByRole('spinbutton',{name:'Коэффициент допработы EXTRA'}).fill('12500');
  await page.getByRole('spinbutton',{name:'Скидка допработы EXTRA'}).fill('1000');
  await expect(page.getByText('Расчёт допработы EXTRA: 11251 коп.')).toBeVisible();
  await page.getByRole('button',{name:'Опубликовать версию допработ'}).click();
  await expect(page.getByRole('heading',{name:'Допработы, версия 1: 11251 коп.'}))
    .toBeVisible();
  await page.goto('/account/auto-estimates?'+query);
  await page.getByRole('button',{name:'Открыть дополнительные работы'}).click();
  await expect(page.getByText('Решение клиента: ОЖИДАЕТ РЕШЕНИЯ')).toBeVisible();
  await page.getByRole('button',{name:'Согласовать дополнительные работы, версия 1'}).click();
  await expect(page.getByText('Решение клиента: APPROVED')).toBeVisible();
  expect(events).toEqual(['EXTRA_PUBLISHED','EXTRA_APPROVED']);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
});
