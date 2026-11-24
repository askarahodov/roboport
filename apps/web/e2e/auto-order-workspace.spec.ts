import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const root='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders/'+order;
const steps={
  services:'EMPTY',inspection:'MISSING',intakeAct:'MISSING',
  estimate:'MISSING',execution:'NOT_STARTED',quality:'NOT_CHECKED',
  technicalDocuments:'DRAFT',
};
const snapshot=()=>({workOrderId:order,syntheticOnly:true,realMoney:false,
  releaseAllowed:false,bookingCanceled:false,steps});
const pageUrl='/staff/auto-order-workspace?'+new URLSearchParams({org,loc,order});

test('one scoped workspace switches real operations, reloads stages on 390/1280',async({page})=>{
  let progress=steps,workspaceReads=0;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname===root+'/workspace'){
      workspaceReads++;
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({...snapshot(),steps:progress})});
    }
    if(url.pathname===root+'/services'||
      url.pathname===root.replace('/orders/'+order,'')+'/service-catalog'){
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    }
    if(url.pathname===root+'/diagnostics'){
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
          inspectionRecorded:false,estimatePublished:false,services:[],items:[]})});
    }
    if(url.pathname===root+'/inspection-qc'){
      return route.fulfill({status:403,contentType:'application/json',
        body:'{"code":"FORBIDDEN"}'});
    }
    throw Error('Unexpected API path '+url.pathname);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto(pageUrl);
  await expect(page.getByRole('heading',{name:'Рабочее место заказ-наряда — DEV'})).toBeVisible();
  await expect(page.getByRole('table',{name:/Серверная сводка/})).toContainText('Нет услуг');
  await expect(page.getByRole('heading',{name:'Услуги заказ-наряда (dev)'})).toBeVisible();
  await expect(page.getByRole('region',{name:'Активный раздел заказ-наряда'})
    .getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await expect(page.getByRole('region',{name:'Активный раздел заказ-наряда'})
    .getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveJSProperty('readOnly',true);
  await page.getByRole('button',{name:/^Диагностика ·/}).click();
  await expect(page.getByRole('heading',{name:'Диагностика по услугам заказ-наряда (dev)'}))
    .toBeVisible();
  await expect(page.getByRole('region',{name:'Активный раздел заказ-наряда'})
    .getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);

  await page.getByRole('button',{name:/^Контроль качества ·/}).click();
  await expect(page.getByRole('region',{name:'Активный раздел заказ-наряда'})
    .getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveJSProperty('readOnly',true);
  await expect(page.getByRole('region',{name:'Активный раздел заказ-наряда'})
    .getByRole('region',{name:'Очередь нарядов на независимый QC'})).toHaveCount(0);
  progress={...steps,services:'SELECTED'};
  await page.getByRole('button',{name:'Обновить этапы с сервера'}).click();
  await expect(page.getByRole('table',{name:/Серверная сводка/})).toContainText('Выбраны услуги');
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.getByRole('button',{name:/^Услуги ·/}).click();
  await expect(page.getByRole('heading',{name:'Услуги заказ-наряда (dev)'})).toBeVisible();
  expect(workspaceReads).toBeGreaterThanOrEqual(2);
});

test('workspace revocation clears every tab and rejects forged stage summary',async({page})=>{
  let denied=false,forged=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname===root+'/workspace'){
      return route.fulfill(denied?{status:403,contentType:'application/json',
        body:'{"code":"FORBIDDEN"}'}:{status:200,contentType:'application/json',
        body:JSON.stringify({...snapshot(),workOrderId:forged?
          'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa':order})});
    }
    if(url.pathname===root+'/services'||
      url.pathname===root.replace('/orders/'+order,'')+'/service-catalog'){
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    }
    throw Error('Unexpected API path '+url.pathname);
  });
  await page.goto(pageUrl);
  await expect(page.getByRole('table',{name:/Серверная сводка/})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить этапы с сервера'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('heading',{name:'Услуги заказ-наряда (dev)'})).toHaveCount(0);
  await expect(page.getByRole('table',{name:/Серверная сводка/})).toHaveCount(0);
  denied=false;forged=true;
  await page.getByRole('button',{name:'Открыть заказ-наряд'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось проверить этапы');
  await expect(page.getByRole('heading',{name:'Услуги заказ-наряда (dev)'})).toHaveCount(0);
});

test('workspace input rejects invalid locators before any API call',async({page})=>{
  let called=0;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    called++;return route.fulfill({status:500,body:'unexpected'});
  });
  await page.goto('/staff/auto-order-workspace');
  await expect(page.getByRole('button',{name:'Открыть заказ-наряд'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID заказ-наряда рабочего места'}).fill('INVALID');
  await expect(page.getByRole('button',{name:'Открыть заказ-наряд'})).toBeDisabled();
  expect(called).toBe(0);
});


test('assignment tab shows actions only for the locked order',async({page})=>{
  const other='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const mechanic='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const appointment='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  const work=(id:string,assignmentId:string)=>({
    assignmentId,workOrderId:id,mechanicId:mechanic,
    startsAt:'2030-01-20T09:00:00+00:00',
    endsAt:'2030-01-20T10:00:00+00:00',
    status:'OFFERED',acceptedAt:null,canRespond:false,
  });
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path===root+'/workspace')return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify(snapshot())});
    if(path===root+'/services'||path===root.replace('/orders/'+order,'')+'/service-catalog')
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    if(path.endsWith('/assignments')){
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({
          syntheticOnly:true,releaseAllowed:false,organizationId:org,locationId:loc,
          canOffer:true,items:[work(order,appointment),
            work(other,'dddddddd-dddd-4ddd-8ddd-dddddddddddd')],
          withdrawPage:0,withdrawHasMore:false,
          withdrawable:[{...work(other,'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee')}],
          releasePage:0,releaseHasMore:false,
          releasable:[{...work(other,'ffffffff-ffff-4fff-8fff-ffffffffffff'),
            status:'ACCEPTED',acceptedAt:'2030-01-19T10:00:00+00:00'}],
        })});
    }
    throw Error('Unexpected API path '+path);
  });
  await page.goto(pageUrl);
  await expect(page.getByRole('table',{name:/Серверная сводка/})).toBeVisible();
  await page.getByRole('button',{name:/^Назначения ·/}).click();
  const panel=page.getByRole('region',{name:'Активный раздел заказ-наряда'});
  await expect(panel.getByRole('heading',{name:'Назначения механиков'})).toBeVisible();
  await expect(panel.getByRole('textbox',{name:'UUID заказ-наряда'}))
    .toHaveJSProperty('readOnly',true);
  await expect(panel.getByRole('table',{name:'Назначения по времени начала'}))
    .toContainText(order);
  await expect(panel.getByText(other)).toHaveCount(0);
  await expect(panel.getByRole('button',{name:/Отозвать предложение/})).toHaveCount(0);
  await expect(panel.getByRole('button',{name:/Освободить назначение/})).toHaveCount(0);
});

test('workspace labels policy-restricted document stages without leaking status',async({page})=>{
  let progress={...steps,estimate:'APPROVED',technicalDocuments:'POSTED'};
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const url=new URL(route.request().url());
    if(url.pathname===root+'/workspace')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({...snapshot(),steps:progress}),
    });
    if(url.pathname===root+'/services'||
      url.pathname===root.replace('/orders/'+order,'')+'/service-catalog')
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    throw Error('Unexpected API path '+url.pathname);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto(pageUrl);
  const table=page.getByRole('table',{name:/Серверная сводка/});
  const estimate=table.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Смета',exact:true})});
  const documents=table.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Технические документы',exact:true})});
  const inspection=table.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Первичный осмотр',exact:true})});
  const intake=table.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Акт приёмки',exact:true})});
  await expect(estimate.getByRole('cell')).toHaveText('Согласована');
  await expect(documents.getByRole('cell')).toHaveText('Технически проведён');

  progress={...progress,estimate:'RESTRICTED'};
  await page.getByRole('button',{name:'Обновить этапы с сервера'}).click();
  await expect(estimate.getByRole('cell')).toHaveText('Недоступно по правам');
  await expect(documents.getByRole('cell')).toHaveText('Технически проведён');

  progress={...progress,technicalDocuments:'RESTRICTED'};
  await page.getByRole('button',{name:'Обновить этапы с сервера'}).click();
  await expect(documents.getByRole('cell')).toHaveText('Недоступно по правам');
  await expect(estimate.getByRole('cell')).toHaveText('Недоступно по правам');
  await expect(table.getByText('Недоступно по правам')).toHaveCount(2);
  await expect(inspection.getByRole('cell')).toHaveText('Не создан');
  await expect(intake.getByRole('cell')).toHaveText('Не создан');
  const nav=page.getByRole('navigation',{name:'Разделы рабочего места'});
  await expect(nav.getByRole('button',{name:'Смета · Недоступно по правам'})).toBeVisible();
  await expect(nav.getByRole('button',{name:'Сверка и документы · Недоступно по правам'})).toBeVisible();
  await expect(nav.getByRole('button',{name:'История · Недоступно по правам'})).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await expect(estimate.getByRole('cell')).toHaveText('Недоступно по правам');
  await expect(documents.getByRole('cell')).toHaveText('Недоступно по правам');
});


test('manager records future maintenance, retries the exact request and withdraws recommendation',async({page})=>{
  const service='44444444-4444-4444-8444-444444444444';
  const id='55555555-5555-4555-8555-555555555555';
  const stamp='2026-11-24T10:00:00Z';
  const date=new Date(Date.now()+180*86400000).toISOString().slice(0,10);
  let items:any[]=[],lost=true;
  const requests:string[]=[];
  const plan=()=>({syntheticOnly:true,releaseAllowed:false,sendAllowed:false,
    workOrderId:order,canManage:true,baselineKm:41700,
    services:[{id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',code:'OTHER',title:'Другая услуга'}],servicesTruncated:true,items});
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(path===root+'/workspace')return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({...snapshot(),steps:{...steps,inspection:'RECORDED'}})});
    if(path===root+'/services'||path===root.replace('/orders/'+order,'')+'/service-catalog')
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    if(path===root+'/recommendations/services'){
      expect(new URL(req.url()).searchParams.get('q')).toBe('SYN-TO');
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,workOrderId:order,truncated:false,items:[{id:service,code:'SYN-TO',title:'Плановая замена масла'}]})});
    }
    if(path===root+'/recommendations'&&req.method()==='GET')
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(plan())});
    if(path===root+'/recommendations'&&req.method()==='POST'){
      const body=req.postDataJSON();requests.push(req.postData()??'');
      expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
      expect(body).toMatchObject({serviceId:service,reasonCode:'PERIODIC',dueDate:date,dueKm:50000});
      items=[{id,serviceId:service,serviceCode:'SYN-TO',title:'Плановая замена масла',
        reasonCode:'PERIODIC',baselineKm:41700,dueDate:date,dueKm:50000,
        createdAt:stamp,status:'ACTIVE',withdrawnAt:null}];
      if(lost){lost=false;return route.abort('failed');}
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({...plan(),replayed:true})});
    }
    if(path===root+'/recommendations/'+id+'/withdraw'){
      items=items.map(x=>({...x,status:'WITHDRAWN',withdrawnAt:stamp}));
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({...plan(),replayed:false})});
    }
    throw Error('Unexpected '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto(pageUrl);
  await page.getByRole('button',{name:/^План следующего ТО ·/}).click();
  const panel=page.getByRole('region',{name:'План следующего обслуживания'});
  await expect(panel.getByText('Показаны первые 100 совпадений.',{exact:false})).toBeVisible();
  await panel.getByLabel('Поиск услуги по коду или названию').fill('SYN-TO');
  await panel.getByRole('button',{name:'Найти услуги'}).click();
  await panel.getByLabel('Услуга для следующего визита').selectOption(service);
  await panel.getByLabel('Рекомендуемая дата').fill(date);
  await panel.getByLabel('Следующий пробег (км, необязательно)').fill('50000');
  await panel.getByRole('button',{name:'Сохранить рекомендацию'}).click();
  await expect(panel.getByText('Результат неизвестен.',{exact:false})).toBeVisible();
  await panel.getByLabel('Следующий пробег (км, необязательно)').fill('52000');
  await panel.getByRole('button',{name:'Повторить исходную операцию'}).click();
  await expect(panel.getByText('Рекомендация записана.')).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toBe(requests[0]);
  await expect(panel.getByRole('listitem')).toContainText(date);
  await panel.getByRole('button',{name:'Отозвать рекомендацию',exact:true}).click();
  await panel.getByRole('button',{name:'Подтвердить отзыв рекомендации'}).click();
  await expect(panel.getByRole('listitem')).toContainText('Отозвана');
  await expect(panel.getByRole('button',{name:'Отозвать рекомендацию',exact:true})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
