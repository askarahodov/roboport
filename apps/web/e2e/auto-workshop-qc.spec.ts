import { test, expect } from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const base='**/api/v1/dev/auto/organizations/**';
type Event={id:string;result:string;findingCode:string;createdAt:string};

test('intake -> mechanic QC FAIL -> PASS, auditable and responsive',async({page})=>{
  const inspection={id:'44444444-4444-4444-8444-444444444444',
    odometerKm:75000,fuelPercent:42,tyresOk:true,lightsOk:false,fluidLeak:false,
    exteriorCode:'DAMAGE',createdAt:'2030-01-01T10:00:00Z'};
  let registered=false;
  let events:Event[]=[];
  const writes:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(base,route=>{
    const request=route.request(),url=new URL(request.url());
    if(request.method()==='GET'){
      const state=!registered?'NOT_INSPECTED':events.length===0?'PENDING_QC':
        events[events.length-1].result==='PASS'?'QC_PASSED':'QC_FAILED';
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        status:state,releaseAllowed:false,inspection:registered?inspection:null,qcEvents:events,
      })});
    }
    expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
    if(url.pathname.endsWith('/inspection')){
      expect(request.postDataJSON()).toEqual({
        odometerKm:75000,fuelPercent:42,tyresOk:true,lightsOk:false,
        fluidLeak:false,exteriorCode:'DAMAGE'});
      registered=true;writes.push('inspection');
    }else{
      const body=request.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      events=[...events,{id:(events.length+1).toString().padStart(8,'0')+
        '-0000-4000-8000-000000000001',result:body.result,
        findingCode:body.findingCode,createdAt:'2030-01-01T10:00:00Z'}];
      writes.push(body.result);
    }
    return route.fulfill({status:201,contentType:'application/json',body:'{"ok":true}'});
  });
  for(const width of [390,1280]){
    await page.setViewportSize({width,height:844});
    await page.goto('/staff/auto-workshop-qc');
    await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
    await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
    await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
    if(!registered){
      await expect(page.getByRole('heading',{name:'Состояние: NOT_INSPECTED'})).toBeVisible();
      await page.getByRole('spinbutton',{name:'Пробег, км'}).fill('75000');
      await page.getByRole('spinbutton',{name:'Топливо, %'}).fill('42');
      await page.getByLabel('Шины проверены').check();
      await page.getByRole('combobox',{name:'Состояние кузова'}).selectOption('DAMAGE');
      await page.getByRole('button',{name:'Зафиксировать осмотр'}).click();
      await expect(page.getByRole('heading',{name:'Состояние: PENDING_QC'})).toBeVisible();
      await page.getByRole('button',{name:'Записать результат QC'}).click();
      await expect(page.getByRole('heading',{name:'Состояние: QC_FAILED'})).toBeVisible();
      await expect(page.getByText('FAIL · REWORK', {exact:false})).toBeVisible();
      await page.getByRole('combobox',{name:'Результат QC'}).selectOption('PASS');
      await page.getByRole('button',{name:'Записать результат QC'}).click();
      await expect(page.getByRole('heading',{name:'Состояние: QC_PASSED'})).toBeVisible();
    } else {
      await expect(page.getByRole('heading',{name:'Состояние: QC_PASSED'})).toBeVisible();
      await expect(page.getByRole('button',{name:'Зафиксировать осмотр'})).toHaveCount(0);
      await expect(page.getByRole('button',{name:'Записать результат QC'})).toHaveCount(0);
    }
    await expect(page.getByText('Выдача не разрешена этим dev-модулем.')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  expect(writes).toEqual(['inspection','FAIL','PASS']);
});

test('inspection and QC console removes old tenant data on 403',async({page})=>{
  let denied=false;
  await page.route(base,route=>route.fulfill(denied?
    {status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'}:
    {status:200,contentType:'application/json',body:JSON.stringify({
      status:'PENDING_QC',releaseAllowed:false,
      inspection:{id:'44444444-4444-4444-8444-444444444444',
        odometerKm:12500,fuelPercent:60,tyresOk:true,lightsOk:true,fluidLeak:false,
        exteriorCode:'CLEAR',createdAt:'2030-01-01T10:00:00Z'},qcEvents:[],
    })}
  ));
  await page.goto('/staff/auto-workshop-qc');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByText('Пробег: 12500 км',{exact:false})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить журнал'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('Пробег: 12500 км',{exact:false})).toHaveCount(0);
  await page.getByRole('textbox',{name:'UUID организации'}).fill('77777777-7777-4777-8777-777777777777');
  await expect(page.getByRole('button',{name:'Записать результат QC'})).toHaveCount(0);
});
