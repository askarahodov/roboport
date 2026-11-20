import { test, expect } from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const userLogin='mechanic.synthetic';
const location='44444444-4444-4444-8444-444444444444';
const invitation='55555555-5555-4555-8555-555555555555';
const token='A'.repeat(43);

test('organization ADMIN creates, revokes and audits a one-time staff invitation',async({page})=>{
  let status:'PENDING'|'REVOKED'='PENDING';
  let created=false;
  const posts:{path:string;body:unknown}[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**/staff',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"items":[]}',
  }));
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**/staff-invitations**',route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname.endsWith('/audit'))return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({items:created?[
        {eventId:'66666666-6666-4666-8666-666666666666',invitationId:invitation,
          actorId:'77777777-7777-4777-8777-777777777777',action:'CREATED',
          createdAt:'2026-11-20T10:00:00Z',targetLogin:userLogin,role:'MECHANIC',locationId:location},
      ]:[]}),
    });
    if(req.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({items:created?[{
        invitationId:invitation,targetLogin:userLogin,role:'MECHANIC',
        locationId:location,status,createdAt:'2026-11-20T10:00:00Z',
        expiresAt:'2026-11-27T10:00:00Z',grantId:null,
      }]:[]}),
    });
    posts.push({path:url.pathname,body:req.postDataJSON()});
    if(url.pathname.endsWith('/revoke')){
      status='REVOKED';
      return route.fulfill({status:200,contentType:'application/json',body:'{"status":"REVOKED"}'});
    }
    expect(req.headers()['x-csrf-token']).toBe('synthetic-csrf');
    created=true;
    return route.fulfill({
      status:201,contentType:'application/json',
      body:JSON.stringify({invitationId:invitation,token,targetLogin:userLogin,
        role:'MECHANIC',locationId:location,expiresAt:'2026-11-27T10:00:00Z'}),
    });
  });

  await page.goto('/staff/auto-admin');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await expect(page.getByRole('heading',{name:'Пригласить сотрудника'})).toBeVisible();
  await page.getByLabel('Логин учётной записи').fill(userLogin);
  await page.getByLabel('Роль приглашения').selectOption('MECHANIC');
  await page.getByLabel('UUID филиала приглашения (необязательно)').fill(location);
  await page.getByLabel('Срок действия').selectOption('7');
  await page.getByRole('button',{name:'Создать приглашение'}).click();
  await expect(page.getByLabel('Одноразовый код приглашения')).toHaveValue(token);
  await expect(page.getByText('Приглашение создано. Одноразовый код показан только сейчас.')).toBeVisible();
  await expect(page.getByText(/mechanic\.synthetic/)).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0]).toEqual({
    path:'/api/v1/dev/auto/organizations/'+org+'/staff-invitations',
    body:{targetLogin:userLogin,role:'MECHANIC',locationId:location,expiresInDays:7},
  });

  await page.getByRole('button',{name:'Отозвать приглашение'}).click();
  await page.getByRole('button',{name:'Подтвердить отзыв приглашения'}).click();
  await expect(page.getByText(/Отозвано/)).toBeVisible();
  await expect(page.getByRole('status').filter({hasText:'Приглашение отозвано.'})).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1].path).toBe('/api/v1/dev/auto/organizations/'+org+
    '/staff-invitations/'+invitation+'/revoke');
  await page.getByRole('button',{name:'Показать события приглашений',exact:true}).click();
  await expect(page.getByText(/Создано/)).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('invitee must be authenticated as the invited account before accepting',async({page})=>{
  let permitted=false;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route('**/api/v1/dev/auto/staff-invitations/accept',route=>{
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
    expect(route.request().postDataJSON()).toEqual({token});
    return permitted?route.fulfill({
      status:201,contentType:'application/json',
      body:JSON.stringify({status:'ACCEPTED',organizationId:org,
        grantId:invitation,role:'MECHANIC',locationId:location}),
    }):route.fulfill({
      status:403,contentType:'application/json',body:'{"code":"FORBIDDEN"}',
    });
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-invitation');
  await page.getByLabel('Одноразовый код приглашения').fill(token);
  await page.getByRole('button',{name:'Принять приглашение'}).click();
  await expect(page.getByRole('status')).toContainText('Войдите под той учётной записью');
  permitted=true;
  await page.getByRole('button',{name:'Принять приглашение'}).click();
  await expect(page.getByRole('heading',{name:'Доступ активирован'})).toBeVisible();
  await expect(page.getByText('MECHANIC',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
