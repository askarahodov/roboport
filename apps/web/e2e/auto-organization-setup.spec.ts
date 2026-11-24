import { test, expect } from '@playwright/test';

const organization = '11111111-1111-4111-8111-111111111111';
const location = '22222222-2222-4222-8222-222222222222';
const bay = '33333333-3333-4333-8333-333333333333';
const shift = '44444444-4444-4444-8444-444444444444';
const employee = '55555555-5555-4555-8555-555555555555';
const actor = '66666666-6666-4666-8666-666666666666';
const api = '**/api/v1/dev/auto/organizations/**';

function mockWorkshop(page:any) {
  const locations:any[]=[];
  const bays:any[]=[];
  const shifts:any[]=[];
  const audit:any[]=[];
  let eventCounter=0;
  const eventId=()=> '77777777-7777-4777-8777-'+String(++eventCounter).padStart(12,'0');
  return page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  })).then(()=>page.route(api,async(route:any)=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()==='GET'&&path.endsWith('/workshop/audit')){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:audit.slice().reverse()})});
    }
    if(request.method()==='GET'&&path.endsWith('/workshop')){
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,locations,bays,shifts,
        locationsTruncated:false,baysTruncated:false,shiftsTruncated:false,
      })});
    }
    if(request.method()!=='POST')return route.fulfill({status:405});
    expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
    const parts=path.split('/').filter(Boolean);
    const locIndex=parts.indexOf('locations');
    if(path.endsWith('/locations')){
      expect(request.headers()['idempotency-key']).toBeTruthy();
      const body=request.postDataJSON();
      locations.push({locationId:location,code:body.code,timeZone:body.timeZone,
        disabled:false,createdAt:new Date().toISOString()});
      audit.push({eventId:eventId(),locationId:location,actorId:actor,
        entityType:'LOCATION',entityId:location,action:'LOCATION_CREATED',createdAt:new Date().toISOString()});
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({locationId:location})});
    }
    if(locIndex>=0&&parts[locIndex+2]==='bays'&&parts.length===locIndex+3){
      expect(request.headers()['idempotency-key']).toBeTruthy();
      const body=request.postDataJSON();
      bays.push({bayId:bay,locationId:location,code:body.code,disabled:false,createdAt:new Date().toISOString()});
      audit.push({eventId:eventId(),locationId:location,actorId:actor,
        entityType:'BAY',entityId:bay,action:'BAY_CREATED',createdAt:new Date().toISOString()});
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({bayId:bay})});
    }
    if(locIndex>=0&&parts[locIndex+2]==='shifts'&&parts.length===locIndex+3){
      expect(request.headers()['idempotency-key']).toBeTruthy();
      const body=request.postDataJSON();
      shifts.push({shiftId:shift,locationId:location,userId:body.userId,
        startsAt:body.startsAt,endsAt:body.endsAt,canceled:false,createdAt:new Date().toISOString()});
      audit.push({eventId:eventId(),locationId:location,actorId:actor,
        entityType:'SHIFT',entityId:shift,action:'SHIFT_CREATED',createdAt:new Date().toISOString()});
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({shiftId:shift})});
    }
    if(path.endsWith('/cancel')){
      const item=shifts.find(x=>x.shiftId===shift);if(item)item.canceled=true;
      audit.push({eventId:eventId(),locationId:location,actorId:actor,
        entityType:'SHIFT',entityId:shift,action:'SHIFT_CANCELED',createdAt:new Date().toISOString()});
      return route.fulfill({status:200,contentType:'application/json',body:'{"status":"CANCELED"}'});
    }
    if(path.endsWith('/disable')){
      if(parts.includes('bays')){const item=bays.find(x=>x.bayId===bay);if(item)item.disabled=true;}
      else {const item=locations.find(x=>x.locationId===location);if(item)item.disabled=true;}
      return route.fulfill({status:200,contentType:'application/json',body:'{"status":"DISABLED"}'});
    }
    return route.fulfill({status:404});
  }));
}

test('organization ADMIN creates a branch, work bay and shift, then cancels with audit', async ({page},testInfo)=>{
  await page.setViewportSize({width:1280,height:900});
  await mockWorkshop(page);
  await page.goto('/staff/auto-setup');
  await expect(page.getByRole('heading',{name:'Организация, филиалы и посты'})).toBeVisible();
  await page.getByRole('textbox',{name:'UUID организации'}).fill(organization);
  await expect(page.getByRole('heading',{name:'Филиалы',exact:true})).toBeVisible();

  await page.getByLabel('Код филиала').fill('BRANCH_A');
  await page.getByLabel('Часовой пояс IANA').fill('Europe/Berlin');
  await page.getByRole('button',{name:'Создать филиал'}).click();
  await expect(page.getByText('Филиал создан.')).toBeVisible();
  await expect(page.getByRole('cell',{name:'BRANCH_A'})).toBeVisible();

  await page.getByRole('combobox',{name:'Филиал'}).first().selectOption(location);
  await page.getByLabel('Код поста').fill('BAY_1');
  await page.getByRole('button',{name:'Создать пост'}).click();
  await expect(page.getByText('Рабочий пост создан.')).toBeVisible();
  await expect(page.getByRole('cell',{name:'BAY_1'})).toBeVisible();

  await page.getByRole('combobox',{name:'Филиал'}).nth(1).selectOption(location);
  await page.getByLabel('UUID сотрудника').fill(employee);
  await page.getByLabel('Начало (ISO 8601 с offset)').fill('2030-04-10T09:00:00+02:00');
  await page.getByLabel('Окончание (ISO 8601 с offset)').fill('2030-04-10T17:00:00+02:00');
  await page.getByRole('button',{name:'Назначить смену'}).click();
  await expect(page.getByText('Смена создана.')).toBeVisible();
  await expect(page.getByText(employee)).toBeVisible();

  await page.getByRole('button',{name:'Отменить',exact:true}).click();
  await page.getByRole('button',{name:'Подтвердить отмену'}).click();
  await expect(page.getByText('Смена отменена.')).toBeVisible();
  await expect(page.getByRole('cell',{name:'SHIFT_CANCELED'})).toBeVisible();

  await page.getByLabel('Тема оформления').selectOption('light');
  await expect(page.locator('main.app')).not.toHaveClass(/dark/);
  await page.screenshot({path:testInfo.outputPath('auto-organization-setup-light-1280.png'),fullPage:true});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app')).toHaveClass(/dark/);
  await page.screenshot({path:testInfo.outputPath('auto-organization-setup-dark-1280.png'),fullPage:true});
});

test('workshop setup fails closed for a non-admin organization member',async({page})=>{
  await page.route(api,route=>route.fulfill({
    status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}',
  }));
  await page.goto('/staff/auto-setup');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(organization);
  await expect(page.getByRole('alert')).toContainText('роль ADMIN');
  await expect(page.getByRole('button',{name:'Создать филиал'})).toHaveCount(0);
});

test('organization setup remains keyboard-usable without horizontal page overflow on mobile',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});
  await mockWorkshop(page);
  await page.goto('/staff/auto-setup');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(organization);
  await expect(page.getByRole('heading',{name:'Филиалы',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByLabel('Код филиала').focus();
  await expect(page.getByLabel('Код филиала')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Часовой пояс IANA')).toBeFocused();
  await expect(page.getByText('Филиалы ещё не созданы.')).toBeVisible();
  await page.getByLabel('Тема оформления').selectOption('light');
  await expect(page.locator('main.app')).not.toHaveClass(/dark/);
  await page.screenshot({path:testInfo.outputPath('auto-organization-setup-mobile-390.png'),fullPage:true});
});



test('ADMIN reaches later branches bays shifts and audit, then revocation clears structure',async({page})=>{
  const id=(n:number)=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
  const stamp='2030-04-10T09:00:00Z';
  const loc=(n:number)=>({locationId:id(n),code:'BR_'+n,timeZone:'UTC',disabled:false,createdAt:stamp});
  const bayRow=(n:number)=>({bayId:id(n+1000),locationId:id(n),code:'BAY_'+n,disabled:false,createdAt:stamp});
  const shiftRow=(n:number)=>({shiftId:id(n+2000),locationId:id(n),userId:id(n+3000),startsAt:stamp,endsAt:'2030-04-10T10:00:00Z',canceled:false,createdAt:stamp});
  const auditRow=(n:number)=>({eventId:id(n+4000),locationId:id(n),actorId:actor,entityType:'LOCATION',entityId:id(n),action:'LOCATION_CREATED',createdAt:stamp});
  const first=Array.from({length:100},(_,i)=>i+1);
  let denied=false;
  await page.route(api,route=>{
    const url=new URL(route.request().url()),cursor=url.searchParams.get('cursor');
    if(denied)return route.fulfill({status:403,contentType:'application/json',body:'{}'});
    let body:unknown;
    if(url.pathname.endsWith('/workshop/audit')){
      body=cursor?{items:[auditRow(101)],nextCursor:'audit_tail'}:{items:first.map(auditRow),nextCursor:'audit_next'};
    }else if(url.pathname.endsWith('/workshop/page')){
      const kind=url.searchParams.get('kind');
      body={items:[kind==='locations'?loc(101):kind==='bays'?bayRow(101):shiftRow(101)]};
    }else body={syntheticOnly:true,locations:first.map(loc),bays:first.map(bayRow),shifts:first.map(shiftRow),
      locationsTruncated:true,baysTruncated:true,shiftsTruncated:true,
      locationsNextCursor:'loc_next',baysNextCursor:'bay_next',shiftsNextCursor:'shift_next'};
    // The last audit page must have a full page to issue another cursor.
    if(url.pathname.endsWith('/workshop/audit')&&cursor)
      body={items:Array.from({length:100},(_,i)=>auditRow(i+101)),nextCursor:'audit_tail'};
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-setup');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(organization);
  await page.getByRole('button',{name:'Показать ещё филиалы'}).click();
  await expect(page.getByRole('region',{name:'Список филиалов'}).getByRole('cell',{name:'BR_101',exact:true})).toBeVisible();
  await page.getByRole('combobox',{name:'Филиал'}).first().selectOption(id(101));
  await page.getByRole('button',{name:'Показать ещё посты'}).click();
  await expect(page.getByRole('cell',{name:'BAY_101',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Показать ещё смены'}).click();
  await expect(page.getByRole('region',{name:'Список смен'}).getByText(id(3101))).toBeVisible();
  await page.getByRole('button',{name:'Показать ещё события аудита'}).click();
  await expect(page.getByRole('region',{name:'Журнал административных операций'}).getByRole('row')).toHaveCount(201);
  denied=true;
  await page.getByRole('button',{name:'Показать ещё события аудита'}).click();
  await expect(page.getByRole('alert')).toContainText('роль ADMIN');
  await expect(page.getByRole('region',{name:'Список филиалов'})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Журнал административных операций'})).toHaveCount(0);
});
