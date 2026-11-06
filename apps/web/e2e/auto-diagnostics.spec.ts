import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const service='44444444-4444-4444-8444-444444444444';
const findingId='55555555-5555-4555-8555-555555555555';
const revisionId='66666666-6666-4666-8666-666666666666';
const q=new URLSearchParams({org,loc,order}).toString();

test('inspected WorkOrder → mechanic diagnosis → signed quote → customer consent, mobile/desktop',async({page})=>{
  let recorded=false,quoted=false,approved=false;
  const calls:string[]=[];
  const finding={id:findingId,serviceId:service,findingCode:'FAULT_CONFIRMED',
    severityCode:'SOON',actorId:org,createdAt:'2030-03-01T10:00:00Z'};
  const evidence={serviceId:service,findingCode:'FAULT_CONFIRMED',severityCode:'SOON'};
  const services=[{id:service,code:'SYN-DIAG',title:'Диагностика подвески',
    durationMinutes:45}];
  const lines=[{serviceId:service,code:'SYN-DIAG',title:'Диагностика подвески',
    durationMinutes:45,amountMinor:2500}];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const data=req.postDataJSON();
      expect(data.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(path.endsWith('/diagnostics')){
        expect(data).toMatchObject({serviceId:service,
          findingCode:'FAULT_CONFIRMED',severityCode:'SOON'});
        recorded=true;calls.push('diagnostic');
      }else if(path.endsWith('/estimates/decision')){
        expect(data).toMatchObject({revisionId,decision:'APPROVED'});
        approved=true;calls.push('consent');
      }else if(path.endsWith('/estimates')){
        expect(data).toMatchObject({expectedVersion:0,
          prices:[{serviceId:service,amountMinor:2500}]});
        quoted=true;calls.push('quote');
      }else throw Error('Unexpected POST '+path);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true}'});
    }
    if(path.endsWith('/inspection-qc')){
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({status:'PENDING_QC',releaseAllowed:false,
          inspection:{id:findingId,odometerKm:41700,fuelPercent:65,
            tyresOk:true,lightsOk:true,fluidLeak:false,exteriorCode:'CLEAR',
            createdAt:'2030-03-01T09:00:00Z'},qcEvents:[],reworks:[]})});
    }
    if(path.endsWith('/diagnostics')){
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
          inspectionRecorded:true,estimatePublished:quoted,
          services:[{serviceId:service,code:'SYN-DIAG',
            title:'Диагностика подвески',durationMinutes:45}],
          items:recorded?[finding]:[]})});
    }
    if(path.endsWith('/services'))return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({items:services})});
    if(path.endsWith('/estimates'))return route.fulfill({
      status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,releaseAllowed:false,
        latestVersion:quoted?1:0,
        items:quoted?[{id:revisionId,version:1,totalMinor:2500,
          currency:'RUB',digest:'a'.repeat(64),lines,diagnostics:[evidence],
          decision:approved?'APPROVED':null,
          createdAt:'2030-03-01T11:00:00Z',decidedAt:null}]:[],
      })});
    throw Error('Unexpected GET '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-workshop-qc?'+q);
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByRole('heading',{name:'Состояние: PENDING_QC'})).toBeVisible();
  await page.getByRole('link',{name:'диагностическое заключение (dev)'}).click();
  await expect(page.getByRole('heading',{name:'Диагностика по услугам заказ-наряда (dev)'}))
    .toBeVisible();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await page.getByRole('button',{name:'Зафиксировать заключение'}).click();
  await expect(page.getByText('Подтверждена неисправность',{exact:false})).toBeVisible();
  await page.getByRole('link',{name:'Смета и согласование клиента (dev)'}).click();
  await expect(page.getByRole('heading',{name:'Сметы заказ-наряда (dev)'})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await page.getByRole('spinbutton',{name:'Стоимость услуги SYN-DIAG'}).fill('2500');
  await page.getByRole('button',{name:'Опубликовать версию'}).click();
  await expect(page.getByRole('heading',{
    name:'Диагностическое заключение, включённое в эту версию'})).toBeVisible();
  await expect(page.getByText('SYN-DIAG: FAULT_CONFIRMED · SOON')).toBeVisible();
  await page.goto('/account/auto-estimates?'+q);
  await expect(page.getByRole('heading',{name:'Согласование сметы (dev)'})).toBeVisible();
  await expect(page.getByText('SYN-DIAG: FAULT_CONFIRMED · SOON')).toBeVisible();
  await page.getByRole('button',{name:'Согласовать версию 1'}).click();
  await expect(page.getByText('Решение: APPROVED')).toBeVisible();
  expect(calls).toEqual(['diagnostic','quote','consent']);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.goto('/staff/auto-diagnostics?'+q);
  await expect(page.getByText('Смета уже опубликована.',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Зафиксировать заключение'})).toHaveCount(0);
});

test('diagnostic console removes cached findings on revoked scope',async({page})=>{
  let revoked=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>
    route.fulfill(revoked?{status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'}:{status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,releaseAllowed:false,
        inspectionRecorded:true,estimatePublished:false,
        services:[{serviceId:service,code:'PRIVATE-SYN',
          title:'Закрытая услуга',durationMinutes:30}],
        items:[{id:findingId,serviceId:service,findingCode:'FAULT_CONFIRMED',
          severityCode:'URGENT',actorId:org,createdAt:'2030-03-01T10:00:00Z'}]})}));
  await page.goto('/staff/auto-diagnostics?'+q);
  await expect(page.getByText('PRIVATE-SYN',{exact:false})).toBeVisible();
  revoked=true;
  await page.getByRole('button',{name:'Обновить журнал'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('PRIVATE-SYN',{exact:false})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Зафиксировать заключение'})).toHaveCount(0);
});
