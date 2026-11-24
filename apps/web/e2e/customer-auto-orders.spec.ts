import {test,expect} from '@playwright/test';

const customer='00000000-0000-4000-8000-000000000001';
const order='11111111-1111-4111-8111-111111111111';
const organization='22222222-2222-4222-8222-222222222222';
const location='33333333-3333-4333-8333-333333333333';
const revision='44444444-4444-4444-8444-444444444444';
const service='55555555-5555-4555-8555-555555555555';
const stamp='2030-01-01T10:00:00Z';
type Quote={id:string;version:number;currency:'RUB';totalMinor:number;
  digest:string;decision:'APPROVED'|'DECLINED'|null;createdAt:string;decidedAt:string|null;
  lines:{serviceId:string;code:string;title:string;durationMinutes:number;amountMinor:number}[]};
const quote:Quote={id:revision,version:1,currency:'RUB',totalMinor:12500,
  digest:'a'.repeat(64),decision:null,createdAt:stamp,decidedAt:null,
  lines:[{serviceId:service,code:'SYN-SVC',title:'Синтетическая диагностика',
    durationMinutes:30,amountMinor:12500}]};
const card={id:order,organizationId:organization,locationId:location,
  createdAt:stamp,
  vehicle:{make:'Synthetic',model:'Sedan',modelYear:2026,vinLast4:'6543'},
  appointment:{startsAt:stamp,endsAt:'2030-01-01T11:00:00Z',status:'SCHEDULED'}};
function detail(decision:'APPROVED'|'DECLINED'|null=null){
  return {...card,estimates:[{...quote,decision}],latestVersion:1,
    inspection:{odometerKm:20000,fuelPercent:65,tyresOk:true,lightsOk:true,
      fluidLeak:false,exteriorCode:'CLEAR',createdAt:stamp},
    qcStatus:decision==='APPROVED'?'QC_PASSED':'PENDING_QC',
    workStatus:decision==='APPROVED'?'QC_ACCEPTED':'NOT_APPROVED',
    labor:decision==='APPROVED'?[{serviceId:service,title:'Синтетическая диагностика',
      actualMinutes:40}]:[],
    document:decision==='APPROVED'?{id:'66666666-6666-4666-8666-666666666666',
      sha256:'f'.repeat(64),createdAt:stamp,type:'SYNTHETIC_PRE_HANDOVER_PREVIEW'}:null,
    canDecide:decision===null,syntheticOnly:true,legalGatePassed:false,releaseAllowed:false};
}

async function auth(page:import('@playwright/test').Page){
  await page.route('**/api/v1/auth/me',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({subjectId:customer}),
  }));
}

test('authenticated 390/1280 customer discovers own order, reviews quote and approves current version',async({page})=>{
  await auth(page);
  let approved:false|true=false;
  const calls:string[]=[];
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>{
    calls.push('list');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({items:[card],nextCursor:null,
        syntheticOnly:true,releaseAllowed:false})});
  });
  await page.route('**/api/v1/dev/auto/customer/orders/'+order,route=>{
    calls.push('detail');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(detail(approved?'APPROVED':null))});
  });
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"mock-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**/estimates/decision',route=>{
    const request=route.request(),data=request.postDataJSON();
    expect(request.headers()['x-csrf-token']).toBe('mock-csrf');
    expect(data.revisionId).toBe(revision);
    expect(data.decision).toBe('APPROVED');
    expect(data.requestKey).toMatch(/^[0-9a-f-]{36}$/);
    approved=true;calls.push('consent');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({ok:true})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/auto-orders');
  await expect(page.getByRole('heading',{name:'Мои заказ-наряды (dev)'})).toBeVisible();
  const region=page.getByRole('region',{name:'Мои автомобильные заказ-наряды'});
  await expect(region.getByText('Synthetic Sedan')).toBeVisible();
  await expect(region.getByText('VIN ••••6543')).toBeVisible();
  await expect(region.getByText('VIN ••••6543')).toBeVisible();
  await expect(region.getByText('VIN ••••6543')).toHaveCount(1);
  await region.getByRole('button',{name:'Подробнее о заказе '+order}).click();
  await expect(region.getByRole('heading',{name:'Сметы и решения'})).toBeVisible();
  await expect(region.getByText('Версия 1: 12500 коп. (RUB)')).toBeVisible();
  await expect(region.getByText('Синтетическая диагностика: 12500 коп.')).toBeVisible();
  await expect(region.getByText('VIN ••••6543')).toBeVisible();
  await region.getByRole('button',{name:'Согласовать последнюю версию'}).click();
  await expect(region.getByText('Решение: APPROVED')).toBeVisible();
  await expect(region.getByText('Синтетическая диагностика: 40 мин.')).toBeVisible();
  await expect(region.getByText('Технический пакет:',{exact:false})).toBeVisible();
  await expect(region.getByRole('button',{name:'Согласовать последнюю версию'})).toHaveCount(0);
  expect(calls).toContain('consent');
  for(const theme of ['light','dark','auto']){
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption(theme);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  await page.setViewportSize({width:1280,height:844});
  await page.reload();
  await expect(region.getByText('Synthetic Sedan')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  const refresh=region.getByRole('button',{name:'Обновить заказ-наряды'});
  await refresh.focus();await expect(refresh).toBeFocused();
});

test('revocation returns empty list and removes formerly visible history and quote',async({page})=>{
  await auth(page);
  let revoked=false;
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({items:revoked?[]:[card],nextCursor:null,
      syntheticOnly:true,releaseAllowed:false}),
  }));
  await page.route('**/api/v1/dev/auto/customer/orders/'+order,route=>route.fulfill({
    status:revoked?404:200,contentType:'application/json',
    body:revoked?'{"code":"NOT_FOUND"}':JSON.stringify(detail()),
  }));
  await page.goto('/account/auto-orders');
  const region=page.getByRole('region',{name:'Мои автомобильные заказ-наряды'});
  await region.getByRole('button',{name:'Подробнее о заказе '+order}).click();
  await expect(region.getByText('Версия 1: 12500 коп. (RUB)')).toBeVisible();
  revoked=true;
  await region.getByRole('button',{name:'Обновить заказ-наряды'}).click();
  await expect(region.getByText('Заказ-нарядов с подтверждённой связью пока нет.')).toBeVisible();
  await expect(region.getByText('Версия 1: 12500 коп. (RUB)')).toHaveCount(0);
  await expect(region.getByText('Synthetic Sedan')).toHaveCount(0);
});

test('cursor paging revalidates first page, never retaining revoked cards',async({page})=>{
  await auth(page);
  let removed=false;
  const calls:string[]=[];
  const first=Array.from({length:20},(_,i)=>({
    ...card,id:'00000000-0000-4000-8000-'+(i+1).toString(16).padStart(12,'0'),
    vehicle:{...card.vehicle,model:'Sedan '+(i+1)},
  }));
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>{
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    calls.push(cursor??'first');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(removed?
        {items:[],nextCursor:null,syntheticOnly:true,releaseAllowed:false}:
        cursor?{items:[card],nextCursor:null,syntheticOnly:true,releaseAllowed:false}:
        {items:first,nextCursor:'synthetic-next',syntheticOnly:true,releaseAllowed:false})});
  });
  await page.goto('/account/auto-orders');
  const region=page.getByRole('region',{name:'Мои автомобильные заказ-наряды'});
  await expect(region.getByText('Synthetic Sedan 20',{exact:true})).toBeVisible();
  await region.getByRole('button',{name:'Показать ещё заказ-наряды'}).click();
  await expect(region.getByText('Synthetic Sedan',{exact:true})).toHaveCount(1);
  expect(calls.filter(x=>x==='first').length).toBeGreaterThanOrEqual(2);
  removed=true;
  await region.getByRole('button',{name:'Обновить заказ-наряды'}).click();
  await expect(region.getByText('Заказ-нарядов с подтверждённой связью пока нет.')).toBeVisible();
  await expect(region.getByText('Sedan 20')).toHaveCount(0);
});

test('no customer auth means no order or quote requests and no private content',async({page})=>{
  let fetches=0;
  await page.route('**/api/v1/dev/auto/customer/orders**',route=>{
    fetches++;return route.fulfill({status:200,contentType:'application/json',
      body:'{"items":[],"nextCursor":null,"syntheticOnly":true,"releaseAllowed":false}'});
  });
  await page.goto('/account/auto-orders');
  await expect(page.getByRole('alert')).toContainText('403 — доступ запрещён');
  expect(fetches).toBe(0);
});


test('customer books own recommended service, retries exact request and loses access',async({page})=>{
  await auth(page);
  let denied=false,lost=true;
  const bay='77777777-7777-4777-8777-777777777777';
  const slot={startsAt:'2030-07-01T10:00:00Z',endsAt:'2030-07-01T10:30:00Z'};
  const visit={appointmentId:'88888888-8888-4888-8888-888888888888',workOrderId:'99999999-9999-4999-8999-999999999999',bookingId:bay,status:'SCHEDULED',...slot};
  const requests:string[]=[];
  let booked=false;
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({
      items:[card],nextCursor:null,syntheticOnly:true,releaseAllowed:false})}));
  await page.route('**/api/v1/dev/auto/customer/orders/'+order,route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify(detail('APPROVED'))}));
  await page.route('**/api/v1/dev/auto/organizations/**/recommendations/customer',route=>{
    const path=new URL(route.request().url()).pathname;
    expect(path).toBe('/api/v1/dev/auto/organizations/'+organization+'/locations/'+location+
      '/orders/'+order+'/recommendations/customer');
    return route.fulfill({status:denied?403:200,contentType:'application/json',body:denied?'{}':
      JSON.stringify({syntheticOnly:true,releaseAllowed:false,sendAllowed:false,
        workOrderId:order,canManage:false,baselineKm:20000,services:[],items:[{
          id:revision,serviceId:service,serviceCode:'SYN-TO',title:'Плановая замена масла',
          reasonCode:'PERIODIC',baselineKm:20000,dueDate:'2030-07-01',dueKm:30000,
          createdAt:stamp,status:'ACTIVE',withdrawnAt:null,visit:booked?visit:null}]})});
  });
  await page.route('**/api/v1/dev/auto/organizations/**/recommendations/'+revision+'/visit**',route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='POST'){
      requests.push(req.postData()??'');
      expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
      expect(req.postDataJSON()).toMatchObject({bayId:bay,...slot});
      booked=true;
      if(lost){lost=false;return route.abort('failed');}
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,replayed:true,visit})});
    }
    if(url.searchParams.has('localDay')){
      expect(url.searchParams.get('bayId')).toBe(bay);
      expect(url.searchParams.get('localDay')).toBe('2030-07-01');
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,timeZone:'UTC',guaranteed:false,items:[slot]})});
    }
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,timeZone:'UTC',bays:[{id:bay,code:'POST-1'}]})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/auto-orders');
  await page.getByRole('button',{name:'Подробнее о заказе '+order}).click();
  const plan=page.getByRole('region',{name:'План следующего обслуживания'});
  await plan.getByRole('button',{name:'Показать рекомендации'}).click();
  await expect(plan.getByText('Плановая замена масла',{exact:true})).toBeVisible();
  await expect(plan.getByText('2030-07-01',{exact:false})).toBeVisible();
  await expect(plan.getByRole('button',{name:'Сохранить рекомендацию'})).toHaveCount(0);
  await expect(plan.getByRole('button',{name:'Отозвать рекомендацию'})).toHaveCount(0);
  await plan.getByRole('button',{name:'Записаться по рекомендации'}).click();
  const picker=plan.getByRole('region',{name:'Запись по рекомендации'});
  await expect(picker.getByLabel('Пост повторного визита')).toHaveValue(bay);
  await picker.getByRole('button',{name:'Найти время'}).click();
  await picker.getByRole('listitem').getByRole('button').click();
  await picker.getByRole('button',{name:'Подтвердить повторный визит'}).click();
  await expect(picker.getByText('Результат записи неизвестен.',{exact:false})).toBeVisible();
  await picker.getByRole('button',{name:'Повторить запись с тем же ключом'}).click();
  await expect(plan.getByText('Повторный визит записан.')).toBeVisible();
  expect(requests).toHaveLength(2);expect(requests[1]).toBe(requests[0]);
  await expect(plan.getByText('Запись оформлена',{exact:false})).toBeVisible();
  await expect(plan.getByRole('button',{name:'Записаться по рекомендации'})).toHaveCount(0);
  denied=true;
  await plan.getByRole('button',{name:'Обновить рекомендации'}).click();
  await expect(page.getByText('Карточка недоступна: доступ мог быть отозван.')).toBeVisible();
  await expect(plan).toHaveCount(0);
  await expect(page.getByText('Плановая замена масла',{exact:true})).toHaveCount(0);
});


test('отказ CSRF при согласовании скрывает историю и не создаёт повтор решения',async({page})=>{
  await auth(page);
  let posts=0;
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify({
      items:[card],nextCursor:null,syntheticOnly:true,releaseAllowed:false})}));
  await page.route('**/api/v1/dev/auto/customer/orders/'+order,route=>route.fulfill({
    status:200,contentType:'application/json',body:JSON.stringify(detail())}));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**/estimates/decision',route=>{
    posts++;return route.fulfill({status:403,body:'{}'});
  });
  await page.goto('/account/auto-orders');
  await page.getByRole('button',{name:'Подробнее о заказе '+order}).click();
  await page.getByRole('button',{name:'Согласовать последнюю версию'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ к заказ-нарядам запрещён');
  await expect(page.getByText('Synthetic Sedan',{exact:true})).toHaveCount(0);
  await expect(page.getByText('Версия 1: 12500 коп. (RUB)')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Повторить то же решение'})).toHaveCount(0);
  expect(posts).toBe(0);
});


test('повторяющийся курсор заказов блокирует весь непроверенный набор',async({page})=>{
  await auth(page);
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>{
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      items:[cursor?{...card,id:'99999999-9999-4999-8999-999999999999'}:card],
      nextCursor:'same-cursor',syntheticOnly:true,releaseAllowed:false})});
  });
  await page.goto('/account/auto-orders');
  await page.getByRole('button',{name:'Показать ещё заказ-наряды'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить заказы');
  await expect(page.getByText('Synthetic Sedan',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Показать ещё заказ-наряды'})).toHaveCount(0);
});


test('customer answers own survey, verifies receipt and loses access on revoke',async({page})=>{
  await auth(page);
  let answered=false,revoke=false;
  await page.route('**/api/v1/dev/auto/customer/orders?*',route=>route.fulfill({status:200,contentType:'application/json',
    body:JSON.stringify({items:[card],nextCursor:null,syntheticOnly:true,releaseAllowed:false})}));
  await page.route('**/api/v1/dev/auto/customer/orders/'+order,route=>route.fulfill({status:200,contentType:'application/json',
    body:JSON.stringify(detail('APPROVED'))}));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"mock"}'}));
  await page.route('**/survey/customer',route=>{
    const req=route.request();
    if(revoke)return route.fulfill({status:403,body:'{}'});
    if(req.method()==='POST'){
      expect(req.postDataJSON()).toMatchObject({rating:4,reasonCode:'TIMING'});
      answered=true;
    }
    return route.fulfill({status:req.method()==='POST'?201:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,sendAllowed:false,releaseAllowed:false,workOrderId:order,eligible:true,replayed:false,
      survey:{id:revision,createdAt:'2030-01-01T10:00:00Z',expiresAt:'2030-01-31T10:00:00Z',
        status:answered?'ANSWERED':'OPEN',rating:answered?4:null,reasonCode:answered?'TIMING':null,
        resolvedAt:answered?'2030-01-02T10:00:00Z':null}})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/account/auto-orders');
  await page.getByRole('button',{name:'Подробнее о заказе '+order}).click();
  const section=page.getByRole('region',{name:'Опрос после ремонта'});
  await section.getByRole('button',{name:'Открыть опрос'}).click();
  await expect(section.getByText('Ожидает ответа',{exact:true})).toBeVisible();
  const rating=section.getByRole('combobox',{name:'Оценка',exact:true});
  const reason=section.getByRole('combobox',{name:'Причина',exact:true});
  await expect(rating).toBeEnabled();
  await expect(reason).toBeEnabled();
  await rating.selectOption('4');
  await reason.selectOption('TIMING');
  await expect(rating).toHaveValue('4');
  await expect(reason).toHaveValue('TIMING');
  await section.getByRole('button',{name:'Подтвердить оценку'}).click();
  await expect(section.getByText('Оценка: 4 из 5.',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:844});
  revoke=true;
  await section.getByRole('button',{name:'Обновить опрос'}).click();
  await expect(section).toHaveCount(0);
  await expect(page.getByText('Оценка: 4 из 5.',{exact:false})).toHaveCount(0);
});
