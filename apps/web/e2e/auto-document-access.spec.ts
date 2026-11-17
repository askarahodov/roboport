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
        eventId:rules.length===0?'44444444-4444-4444-8444-444444444444':
          '55555555-5555-4555-8555-555555555555',
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
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.screenshot({path:testInfo.outputPath('admin-deny-390-dark.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole('combobox',{name:'Результат политики'}).selectOption('ALLOW');
  await page.getByLabel('История с даты (UTC)').fill('2099-01-01');
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
  await expect(page.getByText('Доступ запрещён или dev API отключён.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Сохранить правило'})).toHaveCount(0);
});

test('FINANCE CASHIER restrictive policy and effective preview never grant by UI',async({page})=>{
  let blocked=false;
  const events:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(path.endsWith('/staff'))return route.fulfill({
      status:200,contentType:'application/json',body:'{"items":[]}',
    });
    if(blocked)return route.fulfill({status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'});
    if(path===base&&request.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:events})});
    if(path===base+'/effective'){
      const params=new URL(request.url()).searchParams;
      expect(params.get('documentKind')).toBe('FINANCE');
      expect(params.get('userId')).toBe(user);
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,subjectToObjectACL:true,
          documentKind:'FINANCE',userId:user,locationId:loc,roles:['CASHIER'],
          capabilities:Object.fromEntries(capabilities.map(cap=>[
            cap,{allowed:cap!=='PRICE_SALES',notBefore:null},
          ]))})});
    }
    if(path===base&&request.method()==='POST'){
      expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
      const body=request.postDataJSON();
      expect(body).toMatchObject({role:'CASHIER',documentKind:'FINANCE',
        capability:'PRICE_SALES',allowed:false,userId:user,locationId:loc});
      events.push({eventId:'55555555-5555-4555-8555-555555555555',
        role:'CASHIER',userId:user,locationId:loc,documentKind:'FINANCE',
        capability:'PRICE_SALES',allowed:false,notBefore:null,
        actorId:user,createdAt:'2030-01-01T10:00:00Z'});
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,ok:true,replayed:false,items:events})});
    }
    throw Error('Unexpected '+request.method()+' '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-admin');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByRole('button',{name:'Показать ограничения документов'}).click();
  await page.getByLabel('Роль политики').selectOption('CASHIER');
  await page.getByLabel('Документ политики').selectOption('FINANCE');
  await page.getByLabel('Действие политики').selectOption('PRICE_SALES');
  await page.getByLabel('UUID филиала правила').fill(loc);
  await page.getByLabel('UUID сотрудника правила').fill(user);
  await page.getByRole('button',{name:'Сохранить правило'}).click();
  await expect(page.getByText('DENY',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Проверить эффективные права'}).click();
  await expect(page.getByRole('listitem').filter({hasText:'PRICE_SALES: Запрещено'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.setViewportSize({width:1280,height:800});
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  blocked=true;
  await page.getByRole('button',{name:'Показать ограничения документов'}).click();
  await expect(page.getByRole('alert')).toContainText('403');
  await expect(page.getByText('DENY',{exact:true})).toHaveCount(0);
});


test('ADMIN просматривает 120 immutable событий через три страницы, обновление сбрасывает курсор',
  async({page},info)=>{
    const history=Array.from({length:120},(_,i)=>({
      eventId:'00000000-0000-4000-8000-'+String(i+1).padStart(12,'0'),
      role:'MANAGER',userId:user,locationId:loc,documentKind:'ESTIMATE',
      capability:'READ',allowed:i%2===0,notBefore:null,
      actorId:user,
      createdAt:new Date(Date.UTC(2030,0,1,0,0,0)-i*1000).toISOString(),
    }));
    const cursors:(string|null)[]=[];
    let saved=0;
    await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
      status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
    }));
    await page.route('**/api/v1/dev/auto/organizations/**',route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.pathname.endsWith('/staff')&&req.method()==='GET')
        return route.fulfill({status:200,contentType:'application/json',
          body:'{"items":[]}'});
      if(url.pathname===base&&req.method()==='POST'){
        saved++;
        expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
        return route.fulfill({status:201,contentType:'application/json',
          body:'{"syntheticOnly":true,"ok":true,"replayed":false,"items":[]}'});
      }
      if(url.pathname!==base||req.method()!=='GET')
        throw Error('Unexpected '+url.pathname);
      expect(url.searchParams.get('limit')).toBe('50');
      const before=url.searchParams.get('before');
      cursors.push(before);
      const index=before?history.findIndex(e=>e.eventId===before)+1:0;
      expect(index).toBeGreaterThanOrEqual(0);
      const rows=history.slice(index,index+50);
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({
          syntheticOnly:true,items:rows,
          ...(index+50<history.length?{nextBefore:rows.at(-1)?.eventId}:{}),
        })});
    });
    await page.setViewportSize({width:390,height:844});
    await page.goto('/staff/auto-admin');
    await page.getByLabel('UUID организации').fill(org);
    await page.getByRole('button',{name:'Показать ограничения документов'}).click();
    const section=page.getByRole('region',{name:'Гранулярные ограничения на документы'});
    await expect(section.getByText('Страница журнала ограничений: 1.')).toBeVisible();
    await expect(section.locator('ol li')).toHaveCount(50);
    const forward=section.getByRole('button',{name:'Более ранние правила'});
    await forward.focus();await page.keyboard.press('Enter');
    await expect(section.getByText('Страница журнала ограничений: 2.')).toBeVisible();
    await expect(section.locator('ol li')).toHaveCount(50);
    await forward.click();
    await expect(section.getByText('Страница журнала ограничений: 3.')).toBeVisible();
    await expect(section.locator('ol li')).toHaveCount(20);
    await expect(forward).toHaveCount(0);
    await expect(section.getByRole('button',{name:'Предыдущая страница правил'})).toBeVisible();
    await page.screenshot({path:info.outputPath('policy-audit-390-page3.png'),
      fullPage:true,animations:'disabled'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(390);
    await section.getByRole('button',{name:'Предыдущая страница правил'}).click();
    await expect(section.getByText('Страница журнала ограничений: 2.')).toBeVisible();
    expect(cursors).toContain(history[49].eventId);
    expect(cursors).toContain(history[99].eventId);
    await page.setViewportSize({width:1280,height:900});
    await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
    await page.screenshot({path:info.outputPath('policy-audit-1280-dark-page2.png'),
      fullPage:true,animations:'disabled'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(1280);
    await section.getByRole('button',{name:'Сохранить правило'}).click();
    await expect.poll(()=>saved).toBe(1);
    await expect(section.getByText('Страница журнала ограничений: 1.')).toBeVisible();
    expect(cursors.at(-1)).toBeNull();
  });

test('ADMIN теряет права на следующей странице: история и действия очищаются',
  async({page})=>{
    let revoked=false;
    const records=Array.from({length:51},(_,i)=>({
      eventId:'00000000-0000-4000-8000-'+String(i+1).padStart(12,'0'),
      role:'MANAGER',userId:user,locationId:loc,documentKind:'ESTIMATE',
      capability:'READ',allowed:false,notBefore:null,
      actorId:user,createdAt:new Date(Date.UTC(2030,0,1)-i*1000).toISOString(),
    }));
    await page.route('**/api/v1/dev/auto/organizations/**',route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.pathname.endsWith('/staff'))return route.fulfill({
        status:200,contentType:'application/json',body:'{"items":[]}'});
      if(revoked)return route.fulfill({
        status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}'});
      if(url.pathname===base)return route.fulfill({
        status:200,contentType:'application/json',body:JSON.stringify({
          syntheticOnly:true,items:records.slice(0,50),
          nextBefore:records[49].eventId,
        })});
      throw Error('Unexpected '+url.pathname);
    });
    await page.goto('/staff/auto-admin');
    await page.getByLabel('UUID организации').fill(org);
    await page.getByRole('button',{name:'Показать ограничения документов'}).click();
    await expect(page.getByText('DENY',{exact:true})).toHaveCount(50);
    revoked=true;
    await page.getByRole('button',{name:'Более ранние правила'}).click();
    await expect(page.getByRole('alert')).toContainText('403');
    await expect(page.getByText('DENY',{exact:true})).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Сохранить правило'})).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Более ранние правила'})).toHaveCount(0);
  });

test('повреждённый nextBefore без полной страницы не отображает исторические правила',
  async({page})=>{
    await page.route('**/api/v1/dev/auto/organizations/**',route=>{
      const path=new URL(route.request().url()).pathname;
      if(path.endsWith('/staff'))return route.fulfill({
        status:200,contentType:'application/json',body:'{"items":[]}'});
      if(path===base)return route.fulfill({
        status:200,contentType:'application/json',body:JSON.stringify({
          syntheticOnly:true,items:[{
            eventId:'00000000-0000-4000-8000-000000000001',
            role:'MANAGER',userId:user,locationId:loc,documentKind:'ESTIMATE',
            capability:'READ',allowed:false,notBefore:null,
            actorId:user,createdAt:'2030-01-01T10:00:00Z',
          }],nextBefore:'00000000-0000-4000-8000-000000000001',
        })});
      throw Error('Unexpected '+path);
    });
    await page.goto('/staff/auto-admin');
    await page.getByLabel('UUID организации').fill(org);
    await page.getByRole('button',{name:'Показать ограничения документов'}).click();
    await expect(page.getByRole('alert')).toContainText('Ошибка загрузки');
    await expect(page.getByText('DENY',{exact:true})).toHaveCount(0);
  });
