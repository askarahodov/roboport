import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const order2='44444444-4444-4444-8444-444444444444';
const booking='55555555-5555-4555-8555-555555555555';
const bay='66666666-6666-4666-8666-666666666666';
const base='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc;
const row=(id:string)=>({workOrderId:id,appointmentId:booking,bayId:bay,
  createdAt:'2026-11-18T10:00:00+00:00',startsAt:'2026-11-19T10:00:00+00:00',
  endsAt:'2026-11-19T11:00:00+00:00',stage:'DRAFT',repairStage:'DRAFT',documentStage:'RESTRICTED',bookingStatus:'SCHEDULED',localDay:'2026-11-19'});

test('order register: scoped lookup, page navigation and link to selected order',async({page})=>{
  const requests:URL[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url()),path=url.pathname;
    if(path===base+'/orders'){
      requests.push(url);
      const filtered=url.searchParams.get('orderId');
      const stage=url.searchParams.get('stage');
      const cursor=url.searchParams.get('cursor');
      const records=stage==='READY'?[]:filtered?[row(filtered)]:
        cursor?[row(order2)]:[row(order)];
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
          items:records,nextCursor:cursor||filtered||stage==='READY'?null:'signed-cursor'})});
    }
    if(path===base+'/service-catalog'||path===base+'/orders/'+order+'/services'){
      return route.fulfill({status:200,contentType:'application/json',
        body:'{"items":[]}'});
    }
    throw Error('unexpected URL '+url);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-order-register');
  await expect(page.getByRole('heading',{name:'Реестр заказ-нарядов — DEV'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('button',{name:'Следующая страница'})).toBeVisible();
  await page.getByRole('button',{name:'Следующая страница'}).click();
  await expect(page.getByRole('table')).toContainText(order2);
  await page.getByRole('button',{name:'Предыдущая страница'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByRole('combobox',{name:'Состояние заказ-наряда'}).selectOption('READY');
  await expect(page.getByText('Заказ-наряды по этому фильтру не найдены.')).toBeVisible();
  await page.getByRole('combobox',{name:'Состояние заказ-наряда'}).selectOption('ALL');
  await page.getByRole('textbox',{name:'Поиск заказ-наряда'}).fill(order);
  await page.getByRole('button',{name:'Найти в реестре'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByRole('button',{name:'Сведения'}).click();
  await expect(page.getByRole('region',{name:'Сведения о выбранном заказ-наряде'})).toContainText(booking);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.getByRole('link',{name:'Открыть услуги выбранного наряда'}).click();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await expect(page.getByRole('textbox',{name:'UUID организации'})).toHaveValue(org);
  await expect(page.getByRole('textbox',{name:'UUID филиала'})).toHaveValue(loc);
  expect(requests.some(x=>x.searchParams.get('stage')==='READY')).toBe(true);
  expect(requests.some(x=>x.searchParams.get('cursor')==='signed-cursor')).toBe(true);
});

test('revoked register denies and clears previously shown order IDs',async({page})=>{
  let revoked=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname!==base+'/orders')throw Error('unexpected route '+url);
    return route.fulfill(revoked?{status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'}:{
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
        items:[row(order)],nextCursor:null}),
    });
  });
  await page.goto('/staff/auto-order-register');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await expect(page.getByRole('table')).toContainText(order);
  revoked=true;
  await page.getByRole('button',{name:'Обновить реестр'}).click();
  await expect(page.getByRole('alert')).toContainText('нет действующего доступа');
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByText(order,{exact:true})).toHaveCount(0);
});


test('register refresh removes previously visible handover stage and filtered rows',async({page})=>{
  let visible=true;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname!==base+'/orders')throw Error('unexpected route '+url);
    const state=visible?'CLOSED_TECHNICAL':'READY';
    const filter=url.searchParams.get('stage');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
        items:filter==='ALL'||filter===state?[{...row(order),stage:state,repairStage:'READY',
          documentStage:visible?'CLOSED_TECHNICAL':'RESTRICTED'}]:[],
        nextCursor:null})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-order-register');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  const table=page.getByRole('table');
  await expect(table.getByRole('cell',{name:'Технически закрыт',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Сведения'}).click();
  visible=false;
  await page.getByRole('button',{name:'Обновить реестр'}).click();
  await expect(table.getByRole('cell',{name:'Работы завершены',exact:true})).toBeVisible();
  await expect(table.getByRole('cell',{name:'Технически закрыт',exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Сведения о выбранном заказ-наряде'})).toHaveCount(0);
  await page.getByRole('combobox',{name:'Состояние заказ-наряда'}).selectOption('CLOSED_TECHNICAL');
  await expect(page.getByText('Заказ-наряды по этому фильтру не найдены.')).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(table).toHaveCount(0);
});


test('workflow register separates repair and document states and keeps branch on return',async({page})=>{
  const calls:URL[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());calls.push(url);
    if(url.pathname===base+'/orders'){
      const q=url.searchParams;
      expect(q.get('view')).toBe('workflow');
      const visible=(q.get('documentStage')==='ALL'||q.get('documentStage')==='RESTRICTED')&&
        (!q.get('fromDay')||(q.get('fromDay')??'')<='2026-11-19');
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,releaseAllowed:false,items:visible?[row(order)]:[],nextCursor:null})});
    }
    return route.fulfill({status:403,body:'{}'});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-order-register?org='+org+'&loc='+loc);
  await expect(page.getByRole('table')).toContainText('Недоступно по правам');
  await page.getByLabel('Состояние документа',{exact:true}).selectOption('POSTED');
  await expect(page.getByText('Заказ-наряды по этому фильтру не найдены.')).toBeVisible();
  await page.getByLabel('Состояние документа',{exact:true}).selectOption('RESTRICTED');
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByLabel('Дата записи с',{exact:true}).fill('2026-11-20');
  await expect(page.getByText('Заказ-наряды по этому фильтру не найдены.')).toBeVisible();
  await page.getByRole('button',{name:'Сбросить все фильтры'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  expect(calls.some(x=>x.searchParams.get('documentStage')==='POSTED')).toBe(true);
  await page.getByRole('button',{name:'Сведения'}).click();
  await page.getByRole('link',{name:'Открыть рабочее место наряда'}).click();
  await page.getByRole('link',{name:'К реестру заказ-нарядов'}).click();
  await expect(page.getByRole('textbox',{name:'UUID организации'})).toHaveValue(org);
  await expect(page.getByRole('textbox',{name:'UUID филиала'})).toHaveValue(loc);
});


test('register filters and workspace tab survive navigation and refresh without reusing a cursor',async({page})=>{
  const calls:URL[]=[];
  let revoked=false;
  const orderRoot=base+'/orders/'+order;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());calls.push(url);
    if(url.pathname===base+'/orders'){
      if(revoked)return route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,releaseAllowed:false,items:[row(order)],nextCursor:null})});
    }
    if(url.pathname===orderRoot+'/workspace')return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({workOrderId:order,syntheticOnly:true,realMoney:false,releaseAllowed:false,
        bookingCanceled:false,steps:{services:'EMPTY',inspection:'MISSING',intakeAct:'MISSING',
          estimate:'MISSING',execution:'NOT_STARTED',quality:'NOT_CHECKED',technicalDocuments:'DRAFT'}})});
    if(url.pathname===base+'/service-catalog'||url.pathname===orderRoot+'/services')
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    if(url.pathname===orderRoot+'/diagnostics')return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,inspectionRecorded:false,
        estimatePublished:false,services:[],items:[]})});
    throw Error('unexpected route '+url.pathname);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-order-register?org='+org+'&loc='+loc);
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByLabel('Состояние заказ-наряда',{exact:true}).selectOption('DRAFT');
  await page.getByLabel('Этап ремонта',{exact:true}).selectOption('DRAFT');
  await page.getByLabel('Состояние документа',{exact:true}).selectOption('RESTRICTED');
  await page.getByLabel('Состояние записи',{exact:true}).selectOption('SCHEDULED');
  await page.getByLabel('Дата записи с',{exact:true}).fill('2026-11-19');
  await page.getByLabel('Дата записи по',{exact:true}).fill('2026-11-19');
  await page.getByLabel('Поиск заказ-наряда',{exact:true}).fill(order);
  await page.getByRole('button',{name:'Найти в реестре'}).click();
  await expect.poll(()=>new URL(page.url()).searchParams.get('orderId')).toBe(order);
  await expect(page.getByRole('table')).toContainText(order);
  const saved=new URL(page.url()).searchParams.toString();
  await page.reload();
  await expect(page.getByLabel('Состояние документа',{exact:true})).toHaveValue('RESTRICTED');
  await expect(page.getByLabel('Поиск заказ-наряда',{exact:true})).toHaveValue(order);
  await expect(page.getByRole('table')).toContainText(order);
  await page.getByRole('button',{name:'Сведения'}).click();
  await page.getByRole('link',{name:'Открыть рабочее место наряда'}).click();
  await page.getByRole('button',{name:/^Диагностика ·/}).click();
  await expect.poll(()=>new URL(page.url()).searchParams.get('tab')).toBe('diagnostics');
  await page.reload();
  await expect(page.getByRole('heading',{name:'Диагностика по услугам заказ-наряда (dev)'})).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading',{name:'Услуги заказ-наряда (dev)'})).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading',{name:'Диагностика по услугам заказ-наряда (dev)'})).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  revoked=true;
  await page.getByRole('link',{name:'К реестру заказ-нарядов'}).click();
  await expect.poll(()=>new URL(page.url()).searchParams.toString()).toBe(saved);
  await expect(page.getByRole('alert')).toContainText('нет действующего доступа');
  await expect(page.getByRole('table')).toHaveCount(0);
  const last=calls.filter(x=>x.pathname===base+'/orders').pop()!;
  for(const [key,value] of new URLSearchParams(saved)){
    if(key!=='org'&&key!=='loc')expect(last.searchParams.get(key)).toBe(value);
  }
  expect(last.searchParams.has('cursor')).toBe(false);
});

test('invalid link filters do not broaden the register and untrusted return context is discarded',async({page})=>{
  const calls:URL[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());calls.push(url);
    if(url.pathname===base+'/orders')return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,items:[row(order)],nextCursor:null})});
    return route.fulfill({status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
  });
  await page.goto('/staff/auto-order-register?'+new URLSearchParams({org,loc,documentStage:'INVALID'}));
  await expect(page.getByRole('alert')).toContainText('Неверные параметры ссылки');
  expect(calls).toHaveLength(0);
  await page.getByRole('button',{name:'Сбросить все фильтры'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  const foreign='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const candidate=new URLSearchParams({org:foreign,loc,stage:'READY',cursor:'secret-cursor',return:'https://example.org'});
  await page.goto('/staff/auto-order-workspace?'+new URLSearchParams({
    org,loc,order,tab:'unknown',register:candidate.toString()}));
  const back=page.getByRole('link',{name:'К реестру заказ-нарядов'});
  await expect(back).toHaveAttribute('href','/staff/auto-order-register?'+new URLSearchParams({org,loc}));
  await back.click();
  await expect(page.getByRole('table')).toContainText(order);
  expect(new URL(page.url()).searchParams.has('cursor')).toBe(false);

  // Даже контекст своего филиала не может переносить произвольные параметры.
  const local=new URLSearchParams({org,loc,stage:'DRAFT',cursor:'secret-cursor',
    session:'secret-session',return:'https://example.org'});
  await page.goto('/staff/auto-order-workspace?'+new URLSearchParams({
    org,loc,order,register:local.toString()}));
  await expect(page.getByRole('link',{name:'К реестру заказ-нарядов'}))
    .toHaveAttribute('href','/staff/auto-order-register?'+new URLSearchParams({org,loc,stage:'DRAFT'}));
  await page.getByRole('link',{name:'К реестру заказ-нарядов'}).click();
  await expect(page.getByRole('table')).toContainText(order);
  expect(new URL(page.url()).searchParams.has('cursor')).toBe(false);
  expect(new URL(page.url()).searchParams.has('session')).toBe(false);
});
