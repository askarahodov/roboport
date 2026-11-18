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
