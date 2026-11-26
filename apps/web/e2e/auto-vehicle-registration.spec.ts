import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const customer='33333333-3333-4333-8333-333333333333';
const vehicle='44444444-4444-4444-8444-444444444444';
const link='55555555-5555-4555-8555-555555555555';
const vin='SYN12345678901234';

test('регистрация атомарно создаёт pending, повторяет ключ, правит описание и очищает scope',async({page})=>{
  await page.route('**/api/v1/dev/staff/access',route=>route.fulfill({status:200,
    contentType:'application/json',body:JSON.stringify({roles:['INTAKE']})}));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({status:200,
    contentType:'application/json',body:'{"csrfToken":"local-test-csrf"}'}));
  const keys:string[]=[];
  let revision=1,deny=false;
  await page.route('**/vehicle-registrations**',route=>{
    const req=route.request();
    if(req.method()==='GET'){
      if(deny)return route.fulfill({status:403,body:'{}'});
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        vehicleId:vehicle,revision,make:'Synthetic',model:'Revised',variant:null,modelYear:2026,vinLast4:'1234',editable:true})});
    }
    const data=req.postDataJSON();
    expect(req.headers()['x-csrf-token']).toBe('local-test-csrf');
    keys.push(req.headers()['idempotency-key']);
    if(req.url().endsWith('/revisions')){
      expect(data).toEqual({locationId:loc,make:'Synthetic',model:'Revised',variant:null,modelYear:2026,expectedRevision:1});
      revision=2;
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({vehicleId:vehicle,linkId:link,revision,status:'PENDING'})});
    }
    expect(data).toEqual({locationId:loc,customerId:customer,vin,make:'Synthetic',model:'Test',variant:null,modelYear:2026});
    if(keys.length===1)return route.fulfill({status:503,body:'{}'});
    return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({vehicleId:vehicle,linkId:link,revision,status:'PENDING'})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff');
  const panel=page.getByRole('region',{name:'Регистрация автомобиля в филиале'});
  await panel.getByLabel('UUID организации регистрации').fill(org);
  await panel.getByLabel('UUID филиала регистрации').fill(loc);
  await panel.getByLabel('UUID клиента для регистрации').fill(customer);
  await panel.getByLabel('VIN нового автомобиля (необязательно)').fill(vin);
  await panel.getByLabel('Марка автомобиля').fill('Synthetic');
  await panel.getByLabel('Модель автомобиля').fill('Test');
  await panel.getByLabel('Модельный год автомобиля').fill('2026');
  await panel.getByRole('button',{name:'Зарегистрировать автомобиль и связь'}).click();
  await expect(panel.getByRole('alert')).toContainText('Результат неизвестен');
  await expect(panel.getByLabel('UUID филиала регистрации')).toBeDisabled();
  await panel.getByRole('button',{name:'Повторить регистрацию с прежним ключом'}).click();
  await expect(panel.getByRole('status')).toContainText('Автомобиль зарегистрирован');
  expect(keys[0]).toBe(keys[1]);
  await expect(page.getByLabel('UUID связи',{exact:true})).toHaveValue(link);
  await expect(panel.getByLabel('VIN нового автомобиля (необязательно)')).toHaveCount(0);
  await expect(panel.getByText('VIN ••••1234',{exact:false})).toBeVisible();
  await panel.getByLabel('Модель автомобиля').fill('Revised');
  await panel.getByRole('button',{name:'Сохранить новую версию карточки'}).click();
  await expect(panel.getByRole('status')).toContainText('Новая версия карточки сохранена');
  expect(keys[2]).not.toBe(keys[1]);
  deny=true;
  await panel.getByRole('button',{name:'Прочитать карточку регистрации'}).click();
  await expect(panel.getByRole('alert')).toContainText('Карточка недоступна');
  await expect(panel.getByLabel('Модель автомобиля')).toHaveCount(0);
  await expect(panel.getByText('VIN ••••1234',{exact:false})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
