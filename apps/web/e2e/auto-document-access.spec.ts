import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const other='99999999-9999-4999-8999-999999999999';
const loc='22222222-2222-4222-8222-222222222222';
const user='33333333-3333-4333-8333-333333333333';
const base='/api/v1/dev/auto/organizations/'+org+'/access-policies';
const role='MANAGER',kind='ESTIMATE';
const capabilities=['READ','CREATE','EDIT','CLOSE','WRITE','PRICE_SALES',
                    'PRICE_PURCHASE','DISCOUNT'];

test('ADMIN creates restricted role/employee rule, audits, previews effective access at 390/1280',async({page},testInfo)=>{
  const rules:any[]=[];
  let previewReads=0;
  const submits:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const path=new URL(route.request().url()).pathname,request=route.request();
    if(path.endsWith('/staff')&&request.method()==='GET')
      return route.fulfill({status:200,contentType:'application/json',body:'{"items":[]}'});
    if(path===base&&request.method()==='GET')
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,items:[...rules].reverse()})});
    if(path===base+'/effective'&&request.method()==='GET'){
      previewReads++;
      const query=new URL(request.url()).searchParams;
      expect(query.get('userId')).toBe(user);
      expect(query.get('locationId')).toBe(loc);
      const latest=rules.at(-1);
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,subjectToObjectACL:true,
          userId:user,locationId:loc,documentKind:kind,roles:[role],
          capabilities:Object.fromEntries(capabilities.map(cap=>[cap,{
            allowed:cap==='READ'?latest?.allowed??true:true,
            notBefore:cap==='READ'?latest?.notBefore??null:null,
          }]))})});
    }
    if(path===base&&request.method()==='POST'){
      expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
      const body=request.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      expect(body).toMatchObject({role,documentKind:kind,capability:'READ',
        locationId:loc,userId:user});
      submits.push(body);
      rules.push({
        eventId:'44444444-4444-4444-8444-444444444444',
        role,userId:user,locationId:loc,documentKind:kind,
        capability:'READ',allowed:body.allowed,notBefore:body.notBefore,
        actorId:user,createdAt:'2030-01-01T10:00:00Z',
      });
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,ok:true,replayed:false,items:rules})});
    }
    throw Error('Unexpected '+request.method()+' '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await expect(page.getByText('Активных назначений нет.')).toBeVisible();
  await page.getByRole('button',{name:'Показать ограничения документов'}).click();
  await expect(page.getByText('Для этой организации нет специальных ограничений.')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID филиала правила'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID сотрудника правила'}).fill(user);
  await page.getByRole('button',{name:'Сохранить правило'}).click();
  await expect(page.getByText('DENY',{exact:true})).toBeVisible();
  expect(submits).toHaveLength(1);
  expect(submits[0]).toMatchObject({allowed:false,notBefore:null});
  await page.getByRole('button',{name:'Проверить эффективные права'}).click();
  await expect(page.getByRole('listitem').filter({hasText:'READ: Запрещено'})).toBeVisible();
  expect(previewReads).toBe(1);
  await page.screenshot({path:testInfo.outputPath('admin-deny-390-dark.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole('combobox',{name:'Результат политики'}).selectOption('ALLOW');
  await page.getByRole('textbox',{name:'История с даты (UTC)'}).fill('2099-01-01');
  await page.getByRole('button',{name:'Сохранить правило'}).click();
  await expect.poll(()=>submits.length).toBe(2);
  expect(submits[1].notBefore).toBe('2099-01-01T00:00:00.000Z');
  await page.getByRole('button',{name:'Проверить эффективные права'}).click();
  await expect(page.getByRole('listitem').filter({hasText:'READ: Разрешено'})).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.screenshot({path:testInfo.outputPath('admin-allowed-1280-dark.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('lost request reuses same rule key; 403 clears previous sensitive audit',async({page})=>{
  let attempts=0,revoked=false;
  const bodies:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(revoked||path.includes(other))return route.fulfill({
      status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}',
    });
    if(path.endsWith('/staff'))return route.fulfill({
      status:200,contentType:'application/json',body:'{"items":[]}',
    });
    if(path===base&&request.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:'{"syntheticOnly":true,"items":[]}',
    });
    if(path===base&&request.method()==='POST'){
      bodies.push(request.postData()??'');attempts++;
      if(attempts===1)return route.abort('failed');
      return route.fulfill({status:200,contentType:'application/json',
        body:'{"syntheticOnly":true,"ok":true,"replayed":true,"items":[]}'});
    }
    throw Error('Unexpected '+path);
  });
  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await expect(page.getByText('Активных назначений нет.')).toBeVisible();
  await page.getByRole('button',{name:'Показать ограничения документов'}).click();
  await page.getByRole('button',{name:'Сохранить правило'}).click();
  await expect(page.getByRole('button',{name:'Повторить исходное правило'})).toBeVisible();
  await page.getByRole('button',{name:'Повторить исходное правило'}).click();
  await expect.poll(()=>attempts).toBe(2);
  expect(bodies[0]).toBe(bodies[1]);
  revoked=true;
  await page.getByRole('textbox',{name:'UUID организации'}).fill(other);
  await expect(page.getByRole('alert',{name:''}).first()).toBeVisible();
  await expect(page.getByRole('button',{name:'Сохранить правило'})).toHaveCount(0);
});
