import { test,expect } from '@playwright/test';
const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const service='44444444-4444-4444-8444-444444444444';
const lot='55555555-5555-4555-8555-555555555555';
const issue='66666666-6666-4666-8666-666666666666';
const root='**/api/v1/dev/auto/organizations/**';
type Labor={serviceId:string;actualMinutes:number;createdAt:string};
type Material={issueId:string;lotId:string;skuId:string;quantity:number;received:boolean};

test('approved -> labor -> lot issue -> handoff -> finish, responsive',async({page})=>{
  let phase='APPROVED', labor:Labor[]=[],materials:Material[]=[];
  const calls:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route(root,route=>{
    const req=route.request();
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      const path=new URL(req.url()).pathname;
      if(path.endsWith('/start')){phase='IN_PROGRESS';calls.push('START');}
      else if(path.endsWith('/labor')){
        expect(body).toMatchObject({serviceId:service,actualMinutes:40});
        labor=[{serviceId:service,actualMinutes:40,createdAt:'2030-01-01T10:00:00Z'}];
        calls.push('LABOR');
      }else if(path.endsWith('/materials/issue')){
        expect(body).toMatchObject({lotId:lot,quantity:2});
        materials=[{issueId:issue,lotId:lot,skuId:lot,quantity:2,received:false}];
        calls.push('ISSUE');
      }else if(path.endsWith('/materials/receive')){
        expect(body).toMatchObject({issueId:issue});
        materials[0].received=true;calls.push('RECEIVE');
      }else if(path.endsWith('/finish')){phase='READY_FOR_QC';calls.push('FINISH');}
      return route.fulfill({status:201,contentType:'application/json',body:'{"ok":true}'});
    }
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      status:phase,approvedVersion:1,approvedTotalMinor:12500,
      requiredServices:[{serviceId:service,code:'SVC-SYN',title:'Synthetic maintenance',
                         durationMinutes:30}],
      labor,materials,qcStatus:'PENDING_QC',
      executionId:phase==='APPROVED'?null:order,syntheticOnly:true,releaseAllowed:false,
    })});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-execution');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByRole('heading',{name:'Этап: APPROVED'})).toBeVisible();
  await page.getByRole('button',{name:'Начать работы (MANAGER)'}).click();
  await expect(page.getByRole('heading',{name:'Этап: IN_PROGRESS'})).toBeVisible();
  await page.getByRole('spinbutton',{name:'Фактические минуты SVC-SYN'}).fill('40');
  await page.getByRole('button',{name:'Зафиксировать работу'}).click();
  await expect(page.getByText('выполнено 40 мин.',{exact:false})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID партии'}).fill(lot);
  await page.getByRole('spinbutton',{name:'Количество'}).fill('2');
  await page.getByRole('button',{name:'Выдать из резерва (WAREHOUSE)'}).click();
  await expect(page.getByText('ожидает механика',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Завершить выполнение (MECHANIC)'})).toBeDisabled();
  await page.getByRole('combobox',{name:'Выдача для получения'}).selectOption(issue);
  await page.getByRole('button',{name:'Подтвердить получение (MECHANIC)'}).click();
  await expect(page.getByText('получена',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Завершить выполнение (MECHANIC)'}).click();
  await expect(page.getByRole('heading',{name:'Этап: READY_FOR_QC'})).toBeVisible();
  await expect(page.getByRole('link',{name:'Осмотр и QC (dev)'})).toBeVisible();
  await expect(page.getByText('Выдача автомобиля по-прежнему запрещена.',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:844});
  await page.reload();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByRole('heading',{name:'Этап: READY_FOR_QC'})).toBeVisible();
  expect(calls).toEqual(['START','LABOR','ISSUE','RECEIVE','FINISH']);
});

test('execution UI hides stale labor and stock after access revocation',async({page})=>{
  let denied=false;
  await page.route(root,route=>route.fulfill(denied?{
    status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'
  }:{status:200,contentType:'application/json',body:JSON.stringify({
    status:'IN_PROGRESS',approvedVersion:1,approvedTotalMinor:7500,
    requiredServices:[{serviceId:service,code:'PRIVATE-SYN',title:'Synthetic',
       durationMinutes:30}],
    labor:[],materials:[],qcStatus:'PENDING_QC',
    executionId:order,syntheticOnly:true,releaseAllowed:false,
  })}));
  await page.goto('/staff/auto-execution');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByText('PRIVATE-SYN',{exact:false})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить исполнение'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('PRIVATE-SYN',{exact:false})).toHaveCount(0);
});
