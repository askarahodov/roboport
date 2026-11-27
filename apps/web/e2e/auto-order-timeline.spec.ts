import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const evidence='44444444-4444-4444-8444-444444444444';
const evidence2='55555555-5555-4555-8555-555555555555';
const root='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders/'+order;
const steps={services:'EMPTY',inspection:'MISSING',intakeAct:'MISSING',
  estimate:'MISSING',execution:'NOT_STARTED',quality:'NOT_CHECKED',
  technicalDocuments:'DRAFT'};
const workspace=()=>({workOrderId:order,syntheticOnly:true,realMoney:false,
  releaseAllowed:false,bookingCanceled:false,steps});
const make=(id:string,action:string)=>({evidenceId:id,
  action,createdAt:'2026-11-18T10:00:00+00:00'});
const url='/staff/auto-order-workspace?'+new URLSearchParams({org,loc,order});
const extendedActions={
  "DIAGNOSTIC_RECORDED": "Записан результат диагностики",
  "QC_REWORK_RECORDED": "Зафиксирована доработка после QC",
  "COMPLEX_SELECTED": "Выбран комплекс работ",
  "EXTRA_PUBLISHED": "Опубликована дополнительная смета",
  "EXTRA_APPROVED": "Клиент согласовал дополнительные работы",
  "EXTRA_DECLINED": "Клиент отклонил дополнительные работы",
  "ASSIGNMENT_OFFERED": "Предложено назначение исполнителя",
  "ASSIGNMENT_ACCEPTED": "Исполнитель принял назначение",
  "ASSIGNMENT_DECLINED": "Исполнитель отклонил назначение",
  "ASSIGNMENT_WITHDRAWN": "Предложение назначения отозвано",
  "ASSIGNMENT_RELEASED": "Принятое назначение снято",
  "RECOMMENDATION_CREATED": "Создана рекомендация по обслуживанию",
  "RECOMMENDATION_WITHDRAWN": "Рекомендация отозвана",
  "RECOMMENDATION_VISIT_BOOKED": "Создана запись по рекомендации"
};

test('scoped technical chronology: 390 and 1280, page navigation and dark mode',
  async({page})=>{
    let requested=0;
    await page.route('**/api/v1/dev/auto/organizations/**',route=>{
      const path=new URL(route.request().url());
      if(path.pathname===root+'/workspace')
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify(workspace())});
      if(path.pathname===root+'/services'||
        path.pathname===root.replace('/orders/'+order,'')+'/service-catalog')
        return route.fulfill({status:200,contentType:'application/json',
          body:'{"items":[]}'});
      if(path.pathname===root+'/timeline'){
        requested++;
        const after=path.searchParams.has('cursor');
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify({workOrderId:order,syntheticOnly:true,realMoney:false,
            releaseAllowed:false,items:after?[
              make(evidence2,'ORDER_CREATED')]:[make(evidence,'QC_FAIL'),
                ...Object.keys(extendedActions).map((action,index)=>make(
                  '66666666-6666-4666-8666-'+String(index+1).padStart(12,'0'),action))],
            nextCursor:after?null:'signed-next'})});
      }
      throw Error('unexpected API '+path.pathname);
    });
    await page.setViewportSize({width:390,height:844});
    await page.goto(url);
    await expect(page.getByRole('heading',{name:'Рабочее место заказ-наряда — DEV'}))
      .toBeVisible();
    await page.getByRole('button',{name:/^История ·/}).click();
    const panel=page.getByRole('region',{name:'Активный раздел заказ-наряда'});
    await expect(panel.getByRole('heading',{name:'История действий по заказ-наряду'}))
      .toBeVisible();
    await expect(panel.getByText('Контроль качества не пройден')).toBeVisible();
    for(const label of Object.values(extendedActions))await expect(panel.getByText(label,{exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Следующая страница истории'}).click();
    await expect(panel.getByText('Создан заказ-наряд')).toBeVisible();
    await page.getByRole('button',{name:'Предыдущая страница истории'}).click();
    await expect(panel.getByText('Контроль качества не пройден')).toBeVisible();
    await page.setViewportSize({width:1280,height:900});
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
    await page.getByRole('button',{name:'Обновить историю'}).click();
    await expect(panel.getByText('Контроль качества не пройден')).toBeVisible();
    expect(requested).toBeGreaterThanOrEqual(4);
  });

test('timeline redacts all data on 403 and rejects foreign scoped events',
  async({page})=>{
    let deny=false,wrong=false;
    await page.route('**/api/v1/dev/auto/organizations/**',route=>{
      const path=new URL(route.request().url()).pathname;
      if(path===root+'/workspace')
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify(workspace())});
      if(path===root+'/services'||path===root.replace('/orders/'+order,'')+'/service-catalog')
        return route.fulfill({status:200,contentType:'application/json',
          body:'{"items":[]}'});
      if(path===root+'/timeline'){
        if(deny)return route.fulfill({status:403,contentType:'application/json',
          body:'{"code":"FORBIDDEN"}'});
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify({workOrderId:wrong?evidence2:order,
            syntheticOnly:true,realMoney:false,releaseAllowed:false,
            items:[make(evidence,'ORDER_CREATED')],nextCursor:null})});
      }
      throw Error('unexpected API '+path);
    });
    await page.goto(url);
    await page.getByRole('button',{name:/^История ·/}).click();
    const panel=page.getByRole('region',{name:'Активный раздел заказ-наряда'});
    await expect(panel.getByText(evidence,{exact:true})).toBeVisible();
    deny=true;
    await page.getByRole('button',{name:'Обновить историю'}).click();
    await expect(panel.getByRole('alert')).toContainText('Доступ к истории запрещён');
    await expect(panel.getByText(evidence,{exact:true})).toHaveCount(0);
    deny=false;wrong=true;
    await page.getByRole('button',{name:'Обновить историю'}).click();
    await expect(panel.getByRole('alert')).toContainText('История недоступна');
    await expect(panel.getByText(evidence,{exact:true})).toHaveCount(0);
  });
