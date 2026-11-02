import { test, expect } from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const service='44444444-4444-4444-8444-444444444444';
const base='**/api/v1/dev/auto/organizations/**';
type Rev={id:string;version:number;totalMinor:number;currency:'RUB';digest:string;
  lines:{serviceId:string;code:string;title:string;durationMinutes:number;amountMinor:number}[];
  decision:'APPROVED'|'DECLINED'|null;createdAt:string;decidedAt:string|null};
let revisions:Rev[]=[];
let sent=0;
let approved=0;
const path='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders/'+order;

async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
}

test.beforeEach(async({page})=>{
  revisions=[];sent=0;approved=0;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(base,route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='GET'){
      const response=url.pathname.endsWith('/services')?
        {items:[{id:service,code:'SVC-TEST',title:'TEST SERVICE',durationMinutes:30}]}:
        {items:revisions,latestVersion:revisions.length,syntheticOnly:true,releaseAllowed:false};
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(response)});
    }
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    const payload=req.postDataJSON();
    if(url.pathname.endsWith('/decision')){
      approved++;
      expect(payload.revisionId).toBe(revisions[revisions.length-1].id);
      expect(payload.decision).toBe('APPROVED');
      revisions[revisions.length-1].decision='APPROVED';
    }else{
      sent++;
      expect(payload.expectedVersion).toBe(0);
      expect(payload.prices).toEqual([{serviceId:service,amountMinor:9750}]);
      revisions=[{id:'55555555-5555-4555-8555-555555555555',version:1,
        totalMinor:9750,currency:'RUB',digest:'a'.repeat(64),decision:null,
        createdAt:'2030-01-01T10:00:00Z',decidedAt:null,
        lines:[{serviceId:service,code:'SVC-TEST',title:'TEST SERVICE',
          durationMinutes:30,amountMinor:9750}]}];
    }
    return route.fulfill({status:201,contentType:'application/json',
      body:'{"ok":true,"replayed":false}'});
  });
});

test('two-role automobile estimate publishes exact version and client approves it',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-estimates');
  await scope(page);
  await expect(page.getByText('Опубликованных версий пока нет.')).toBeVisible();
  await page.getByRole('spinbutton',{name:'Стоимость услуги SVC-TEST'}).fill('9750');
  await page.getByRole('button',{name:'Опубликовать версию'}).click();
  await expect(page.getByRole('heading',{name:'Версия 1: 9750 коп. (RUB)'})).toBeVisible();
  await page.setViewportSize({width:1280,height:844});
  await page.goto('/account/auto-estimates');
  await scope(page);
  await expect(page.getByRole('heading',{name:'Версия 1: 9750 коп. (RUB)'})).toBeVisible();
  await page.getByRole('button',{name:'Согласовать версию 1'}).click();
  await expect(page.getByText('Решение: APPROVED')).toBeVisible();
  await expect(page.getByRole('button',{name:'Согласовать версию 1'})).toHaveCount(0);
  await expect(page.getByText('не является юридической электронной подписью',{exact:false})).toBeVisible();
  expect(sent).toBe(1);
  expect(approved).toBe(1);
});

test('client estimate history is removed on revoked session and foreign order',async({page})=>{
  revisions=[{id:'55555555-5555-4555-8555-555555555555',version:1,totalMinor:9750,
    currency:'RUB',digest:'b'.repeat(64),decision:null,
    createdAt:'2030-01-01T10:00:00Z',decidedAt:null,
    lines:[{serviceId:service,code:'SVC-TEST',title:'TEST SERVICE',
      durationMinutes:30,amountMinor:9750}]}];
  await page.goto('/account/auto-estimates');
  await scope(page);
  await expect(page.getByRole('heading',{name:'Версия 1: 9750 коп. (RUB)'})).toBeVisible();
  await page.route(base,route=>route.fulfill({status:403,contentType:'application/json',
    body:'{"code":"FORBIDDEN"}'}));
  await page.getByRole('button',{name:'Обновить версии'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('heading',{name:'Версия 1: 9750 коп. (RUB)'})).toHaveCount(0);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'})
    .fill('99999999-9999-4999-8999-999999999999');
  await expect(page.getByRole('button',{name:'Согласовать версию 1'})).toHaveCount(0);
});
