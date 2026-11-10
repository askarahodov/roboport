import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const actId='44444444-4444-4444-8444-444444444444';
const query=new URLSearchParams({org,loc,order}).toString();
const root='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders/'+order+'/intake-acts';
const digest='a'.repeat(64);
const version={
  id:actId,version:1,vehicleKind:'PASSENGER',sha256:digest,
  createdAt:'2030-01-01T10:15:00Z',actorId:org,
  damages:[{areaCode:'FRONT',damageCode:'SCRATCH',severity:'MINOR'}],
  inventory:['KEYS','SPARE_WHEEL','TOOLS','DOCS','CHARGER'].map(itemCode=>
    ({itemCode,condition:'NOT_CHECKED'})),
  photos:[],review:null as null|{decision:'ACKNOWLEDGED'|'DISPUTED';
    reasonCode:string;actorId:string;fingerprint:string;createdAt:string},
};
const base={syntheticOnly:true,releaseAllowed:false,legalSignatureStatus:'NOT_IMPLEMENTED',
  inspection:{id:actId,odometerKm:75000,fuelPercent:42,exteriorCode:'DAMAGE'},
  locked:false,latestVersion:1,previousVisit:null};

test('client checks exact act, disputes/acknowledges without legal signature; staff sees history',async({page},testInfo)=>{
  let review:null|typeof version.review=null;
  const requests:Array<{decision:string;reasonCode:string;fingerprint:string;requestKey:string}>=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),p=new URL(req.url()).pathname;
    if(p===root&&req.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({...base,locked:!!review&&review.decision==='ACKNOWLEDGED',
        items:[{...version,review}]})});
    if(p===root+'/'+actId+'/review'&&req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const body=req.postDataJSON();
      expect(body.fingerprint).toBe(digest);
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      requests.push(body);
      review={decision:body.decision,reasonCode:body.reasonCode,
        actorId:loc,fingerprint:digest,createdAt:'2030-01-01T10:30:00Z'};
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({ok:true,replayed:false})});
    }
    throw Error('Unexpected request: '+p);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/auto-intake-act?'+query);
  await expect(page.getByRole('heading',{name:'Ознакомление с версией №1 (не подпись)'})).toBeVisible();
  await expect(page.getByText('Техническая отметка клиента отсутствует.')).toBeVisible();
  await page.getByRole('button',{name:'Отметить ознакомление (без подписи)'}).click();
  await expect(page.getByText('Ознакомлен с актом (техническая отметка)',{exact:false}).first()).toBeVisible();
  await expect(page.getByRole('button',{name:'Отметить ознакомление (без подписи)'})).toHaveCount(0);
  await expect(page.getByText('Статус юридической подписи: НЕ РЕАЛИЗОВАНО.',{exact:false}).first()).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({decision:'ACKNOWLEDGED',reasonCode:'NONE',
    fingerprint:digest});
  await page.screenshot({path:testInfo.outputPath('intake-client-ack-390.png'),
    fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:900});
  await page.goto('/staff/auto-intake-act?'+query);
  await expect(page.getByText('Ознакомлен с актом (техническая отметка)',{exact:false}).first()).toBeVisible();
  await expect(page.getByRole('button',{name:'Зафиксировать версию акта'})).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('intake-staff-ack-1280.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('client dispute and lost POST preserve decision/reason and same request key',async({page})=>{
  let attempts=0;
  const bodies:string[]=[];
  let review:null|typeof version.review=null;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),p=new URL(req.url()).pathname;
    if(p===root&&req.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({...base,items:[{...version,review}]})});
    if(p===root+'/'+actId+'/review'&&req.method()==='POST'){
      attempts++;
      const raw=req.postData()??'';
      bodies.push(raw);
      const payload=JSON.parse(raw);
      if(attempts===1)return route.abort('failed');
      review={decision:payload.decision,reasonCode:payload.reasonCode,
        actorId:loc,fingerprint:digest,createdAt:'2030-01-01T10:30:00Z'};
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({ok:true,replayed:true})});
    }
    throw Error('Unexpected request '+p);
  });
  await page.goto('/account/auto-intake-act?'+query);
  await page.getByRole('combobox',{name:'Причина расхождения'}).selectOption('INVENTORY_MISMATCH');
  await page.getByRole('button',{name:'Сообщить о расхождении'}).click();
  await expect(page.getByRole('button',{name:'Повторить тот же запрос'})).toBeVisible();
  await page.getByRole('button',{name:'Повторить тот же запрос'}).click();
  await expect.poll(()=>attempts).toBe(2);
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toBe(bodies[1]);
  expect(JSON.parse(bodies[0])).toMatchObject({
    decision:'DISPUTED',reasonCode:'INVENTORY_MISMATCH',fingerprint:digest});
  await expect(page.getByText('Сообщено о расхождении (INVENTORY_MISMATCH)',{exact:false}).first()).toBeVisible();
});

test('revocation hides act reviews and prevents stale client actions',async({page})=>{
  let denied=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>route.fulfill(
    denied?{status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
      {status:200,contentType:'application/json',
        body:JSON.stringify({...base,items:[version]})}));
  await page.goto('/account/auto-intake-act?'+query);
  await expect(page.getByRole('button',{name:'Отметить ознакомление (без подписи)'})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить журнал актов'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('button',{name:'Отметить ознакомление (без подписи)'})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Акт №1 · PASSENGER'})).toHaveCount(0);
});
