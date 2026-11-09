import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const service='44444444-4444-4444-8444-444444444444';
const policy='88888888-8888-4888-8888-888888888888';
const revision='55555555-5555-4555-8555-555555555555';
const query=new URLSearchParams({org,loc,order}).toString();

test('propose and approve branch tariff, show immutable policy to client',async({page})=>{
  let proposed=false,approved=false,quoted=false;
  const calls:string[]=[];
  const proposal={id:policy,qualificationCode:'MASTER',version:1,
    rateMinorPerHour:10001,maxDiscountBps:1000,
    proposedBy:service,createdAt:'2030-05-01T10:00:00Z',
    approvedBy:null as string|null,approvedAt:null as string|null};
  const snapshot=[{serviceId:service,policyId:policy,version:1,
    qualificationCode:'MASTER',rateMinorPerHour:10001,maxDiscountBps:1000}];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const payload=req.postDataJSON();
      expect(payload.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(path.endsWith('/tariff-policies')){
        expect(payload).toMatchObject({qualificationCode:'MASTER',expectedVersion:0,
          rateMinorPerHour:10001,maxDiscountBps:1000});
        proposed=true;calls.push('PROPOSED');
      }else if(path.endsWith('/tariff-policies/'+policy+'/approve')){
        approved=true;calls.push('APPROVED');
      }else if(path.endsWith('/estimates')){
        expect(payload.workRates).toEqual([{
          serviceId:service,qualificationCode:'MASTER',rateMinorPerHour:10001,
          quantity:2,coefficientBps:12500,discountBps:1000,
        }]);
        quoted=true;calls.push('QUOTED');
      }else throw Error('Unexpected POST '+path);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"replayed":false}'});
    }
    if(path.endsWith('/tariff-policies'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,items:proposed?[{...proposal,
          approvedBy:approved?service:null}]:[],
        active:approved?{MASTER:{...proposal,approvedBy:service}}:{},
      })});
    if(path.endsWith('/services'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({items:[{
        id:service,code:'SYN-RATE',title:'Синтетическая работа',durationMinutes:45,
      }]})});
    if(path.endsWith('/estimates'))return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({syntheticOnly:true,
        releaseAllowed:false,latestVersion:quoted?1:0,items:quoted?[{
          id:revision,version:1,totalMinor:16877,currency:'RUB',
          digest:'a'.repeat(64),createdAt:'2030-05-01T11:00:00Z',
          decidedAt:null,decision:null,diagnostics:[],tariffs:snapshot,
          lines:[{serviceId:service,code:'SYN-RATE',title:'Синтетическая работа',
            durationMinutes:45,pricingMethod:'NORM_HOUR_FACTORED',
            qualificationCode:'MASTER',rateMinorPerHour:10001,quantity:2,
            coefficientBps:12500,discountBps:1000,amountMinor:16877}],
        }]:[]})});
    throw Error('Unexpected GET '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-estimates?'+query);
  await page.getByRole('button',{name:'Открыть тарифы филиала'}).click();
  await page.getByRole('combobox',{name:'Тарифная категория филиала'}).selectOption('MASTER');
  await page.getByRole('spinbutton',{name:'Новая ставка тарифа'}).fill('10001');
  await page.getByRole('spinbutton',{name:'Максимальная скидка тарифа'}).fill('1000');
  await page.getByRole('button',{name:'Предложить тариф'}).click();
  await expect(page.getByRole('button',{name:'Утвердить тариф MASTER, версия 1'}))
    .toBeVisible();
  await page.getByRole('button',{name:'Утвердить тариф MASTER, версия 1'}).click();
  await expect(page.getByText('MASTER: версия 1',{exact:false})).toBeVisible();
  await page.getByRole('combobox',{name:'Способ расчёта стоимости'})
    .selectOption('WORK_FACTORED');
  await page.getByRole('combobox',{name:'Категория тарифа SYN-RATE'}).selectOption('MASTER');
  await page.getByRole('spinbutton',{name:'Ставка нормо-часа SYN-RATE'}).fill('10001');
  await page.getByRole('spinbutton',{name:'Кратность работы SYN-RATE'}).fill('2');
  await page.getByRole('spinbutton',{name:'Коэффициент работы SYN-RATE'}).fill('12500');
  await page.getByRole('spinbutton',{name:'Скидка работы SYN-RATE'}).fill('1000');
  await page.getByRole('button',{name:'Опубликовать версию'}).click();
  await expect(page.getByText('Утверждённые тарифы филиала в версии')).toBeVisible();
  await page.goto('/account/auto-estimates?'+query);
  await expect(page.getByText('Утверждённые тарифы филиала в версии')).toBeVisible();
  await expect(page.getByText('лимит скидки 1000/100 %',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  expect(calls).toEqual(['PROPOSED','APPROVED','QUOTED']);
});
