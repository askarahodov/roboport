import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const baseService='44444444-4444-4444-8444-444444444444';
const complexService='66666666-6666-4666-8666-666666666666';
const part='77777777-7777-4777-8777-777777777777';
const consumable='88888888-8888-4888-8888-888888888888';
const complex='99999999-9999-4999-8999-999999999999';
const componentPart='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const componentConsumable='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const selection='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const revisionId='55555555-5555-4555-8555-555555555555';
const query=new URLSearchParams({org,loc,order}).toString();
const services=[
  {id:baseService,code:'BASE-SVC',title:'Основная услуга',durationMinutes:45},
  {id:complexService,code:'EXTRA-SVC',title:'Услуга комплекса',durationMinutes:35},
];
const materials=[
  {componentId:componentPart,skuId:part,article:'PART-SYN',itemKind:'PART',
    quantity:2,unitPriceMinor:800,amountMinor:1600},
  {componentId:componentConsumable,skuId:consumable,article:'CONSUMABLE-SYN',
    itemKind:'CONSUMABLE',quantity:1,unitPriceMinor:0,amountMinor:0},
];
const detail={id:complex,code:'SYN-COMPLEX',title:'Комплекс ремонта',
  services:[services[1]],materials};
const pricedMaterials=materials.map(m=>({
  selectionId:selection,skuId:m.skuId,article:m.article,itemKind:m.itemKind,
  quantity:m.quantity,unitPriceMinor:m.unitPriceMinor,amountMinor:m.amountMinor,
}));

test('manager creates complex, reserves PART and CONSUMABLE, customer sees exact quote',async({page})=>{
  let created=false,selected=false,quoted=false,approved=false;
  const actions:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    const json=(value:unknown)=>route.fulfill({
      status:200,contentType:'application/json',body:JSON.stringify(value)});
    if(req.method()==='POST'){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      if(path.endsWith('/work-complexes')){
        expect(body).toMatchObject({code:'SYN-COMPLEX',title:'Комплекс ремонта',
          serviceIds:[complexService],materials:[
            {skuId:part,itemKind:'PART',quantity:2,unitPriceMinor:800},
            {skuId:consumable,itemKind:'CONSUMABLE',quantity:1,unitPriceMinor:0},
          ]});
        created=true;actions.push('CREATED');
      }else if(path.endsWith('/orders/'+order+'/complexes')){
        expect(body.complexId).toBe(complex);
        selected=true;actions.push('RESERVED');
      }else if(path.endsWith('/estimates/decision')){
        expect(body).toMatchObject({revisionId,decision:'APPROVED'});
        approved=true;actions.push('APPROVED');
      }else if(path.endsWith('/estimates')){
        expect(body.prices).toEqual([
          {serviceId:baseService,amountMinor:2000},
          {serviceId:complexService,amountMinor:3000},
        ]);
        quoted=true;actions.push('QUOTED');
      }else throw Error('Unexpected POST '+path);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true,"replayed":false}'});
    }
    if(path.endsWith('/work-complexes'))return json({
      items:created?[detail]:[],syntheticOnly:true,
    });
    if(path.endsWith('/orders/'+order+'/complexes'))return json({
      syntheticOnly:true,items:selected?[{
        selectionId:selection,complex:detail,
        materialReceipts:[
          {componentId:componentPart,stockEventId:part},
          {componentId:componentConsumable,stockEventId:consumable},
        ],
      }]:[],
    });
    if(path.endsWith('/service-catalog'))return json({items:services});
    if(path.endsWith('/services'))return json({items:selected?services:services.slice(0,1)});
    if(path.endsWith('/estimates'))return json({syntheticOnly:true,releaseAllowed:false,
      latestVersion:quoted?1:0,items:quoted?[{
        id:revisionId,version:1,totalMinor:6600,currency:'RUB',digest:'a'.repeat(64),
        createdAt:'2030-06-01T12:00:00Z',decidedAt:null,
        decision:approved?'APPROVED':null,diagnostics:[],tariffs:[],
        materialLines:pricedMaterials,lines:[
          {serviceId:baseService,code:'BASE-SVC',title:'Основная услуга',
            durationMinutes:45,amountMinor:2000},
          {serviceId:complexService,code:'EXTRA-SVC',title:'Услуга комплекса',
            durationMinutes:35,amountMinor:3000},
        ],
      }]:[]});
    throw Error('Unexpected GET '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-estimates?'+query);
  await page.getByRole('button',{name:'Открыть комплексы работ'}).click();
  await page.getByRole('textbox',{name:'Код комплекса'}).fill('SYN-COMPLEX');
  await page.getByRole('textbox',{name:'Название комплекса'}).fill('Комплекс ремонта');
  await page.getByRole('checkbox',{name:'Услуга комплекса EXTRA-SVC'}).check();
  await page.getByRole('button',{name:'Добавить материал в комплекс'}).click();
  await page.getByRole('textbox',{name:'SKU материала 1'}).fill(part);
  await page.getByRole('spinbutton',{name:'Количество материала 1'}).fill('2');
  await page.getByRole('spinbutton',{name:'Цена материала 1'}).fill('800');
  await page.getByRole('button',{name:'Добавить материал в комплекс'}).click();
  await page.getByRole('textbox',{name:'SKU материала 2'}).fill(consumable);
  await page.getByRole('combobox',{name:'Тип материала 2'}).selectOption('CONSUMABLE');
  await expect(page.getByRole('spinbutton',{name:'Цена материала 2'})).toBeDisabled();
  await page.getByRole('button',{name:'Создать комплекс'}).click();
  await page.getByRole('button',{name:'Открыть комплексы работ'}).click();
  await page.getByRole('button',{name:'Выбрать комплекс SYN-COMPLEX и зарезервировать'}).click();
  await expect(page.getByRole('spinbutton',{name:'Стоимость услуги EXTRA-SVC'})).toBeVisible();
  await page.getByRole('spinbutton',{name:'Стоимость услуги BASE-SVC'}).fill('2000');
  await page.getByRole('spinbutton',{name:'Стоимость услуги EXTRA-SVC'}).fill('3000');
  await page.getByRole('button',{name:'Опубликовать версию'}).click();
  await expect(page.getByRole('heading',{name:'Версия 1: 6600 коп. (RUB)'})).toBeVisible();
  await expect(page.getByText('PART-SYN — Продаваемая деталь',{exact:false})).toBeVisible();
  await expect(page.getByText('CONSUMABLE-SYN — Расходный материал',{exact:false})).toBeVisible();
  await page.goto('/account/auto-estimates?'+query);
  await expect(page.getByText('Детали и расходные материалы в версии')).toBeVisible();
  await expect(page.getByText('PART-SYN — Продаваемая деталь',{exact:false}))
    .toContainText('1600 коп.');
  await expect(page.getByText('CONSUMABLE-SYN — Расходный материал',{exact:false}))
    .toContainText('0 коп.');
  await page.getByRole('button',{name:'Согласовать версию 1'}).click();
  await expect(page.getByText('Решение: APPROVED')).toBeVisible();
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  expect(actions).toEqual(['CREATED','RESERVED','QUOTED','APPROVED']);
});
