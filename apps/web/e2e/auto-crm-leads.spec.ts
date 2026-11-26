import {test,expect} from '@playwright/test';
 
// These tests exercise lead/task behavior; the new inbox has its own E2E suite.
test.beforeEach(async({page})=>{
  await page.route('**/api/v1/dev/auto/organizations/**/crm/inbox',
    route=>route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null})}));
});


const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const link='33333333-3333-4333-8333-333333333333';
const lead='44444444-4444-4444-8444-444444444444';
const owner='55555555-5555-4555-8555-555555555555';
const appointment='66666666-6666-4666-8666-666666666666';
const path='**/api/v1/dev/auto/organizations/**/crm/leads**';
const item=(stage:'NEW'|'QUALIFIED'|'BOOKED'|'LOST',events:unknown[]=[])=>({
  leadId:lead,customerLinkId:link,ownerId:owner,
  sourceCode:'WEB',status:stage,syntheticOnly:true,
  createdAt:'2030-01-01T10:00:00Z',events,
});
const event=(stage:'QUALIFIED'|'BOOKED'|'LOST',reason:string|null,booking:string|null)=>({
  id:'77777777-7777-4777-8777-777777777777',actorId:owner,stageCode:stage,
  reasonCode:reason,appointmentId:booking,createdAt:'2030-01-01T10:01:00Z',
});

async function scope(page:import('@playwright/test').Page){
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
}

test('CRM creates and qualifies synthetic lead with CSRF, 390/1280 dark',async({page})=>{
  let created=false;
  let qualified=false;
  const posted:unknown[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(path,async route=>{
    if(route.request().method()==='GET'){
      const rows=created?[item(qualified?'QUALIFIED':'NEW',qualified?
        [event('QUALIFIED',null,null)]:[])]:[];
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,items:rows})});
    }
    const body=route.request().postDataJSON();
    posted.push(body);
    expect(route.request().headers()['x-csrf-token']).toBe('synthetic-csrf');
    expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/i);
    if(route.request().url().endsWith('/stages')){
      qualified=true;
      expect(body.stageCode).toBe('QUALIFIED');
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({replayed:false,...item('QUALIFIED',[event('QUALIFIED',null,null)])})});
    }
    created=true;
    expect(body.customerLinkId).toBe(link);
    expect(body.sourceCode).toBe('WEB');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({replayed:false,...item('NEW')})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await expect(page.getByRole('heading',{name:'Обращения и этапы CRM'})).toBeVisible();
  await scope(page);
  await expect(page.getByText('Обращений пока нет.')).toBeVisible();
  await page.getByRole('textbox',{name:'UUID подтверждённой связи'}).fill(link);
  await page.getByLabel('Источник',{exact:true}).selectOption('WEB');
  await page.getByRole('button',{name:'Создать обращение'}).click();
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await page.getByLabel('Обращение').selectOption(lead);
  await expect(page.getByText('Текущий этап: Новое.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Сохранить этап'}).click();
  await expect(page.getByText('Текущий этап: Обработано.',{exact:false})).toBeVisible();
  expect(posted).toHaveLength(2);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await page.getByLabel('Тема оформления').selectOption('dark');
  await expect(page.locator('main.app.dark')).toBeVisible();
});

test('CRM revoked scope clears previous lead and denies staff operations',async({page})=>{
  let denied=false;
  await page.route(path,route=>route.fulfill({
    status:denied?403:200,contentType:'application/json',
    body:denied?'{"code":"FORBIDDEN"}':JSON.stringify({
      syntheticOnly:true,items:[item('NEW')],
    }),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByLabel('Обращение')).toBeVisible();
  denied=true;
  await page.getByRole('textbox',{name:'UUID филиала'})
    .fill('88888888-8888-4888-8888-888888888888');
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
  await expect(page.getByText('Текущий этап:',{exact:false})).toHaveCount(0);
});

test('CRM rejects malformed lead response without displaying untrusted owners',async({page})=>{
  await page.route(path,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[{
      ...item('NEW'),ownerId:'not-a-uuid',
    }]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить обращения.');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
});

test('CRM booked stage requires a scoped appointment UUID and qualified stage',async({page})=>{
  await page.route(path,route=>route.fulfill({
    status:200,contentType:'application/json',
    body:JSON.stringify({syntheticOnly:true,items:[item('QUALIFIED',
      [event('QUALIFIED',null,null)])]}),
  }));
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('Обращение').selectOption(lead);
  await page.getByLabel('Этап',{exact:true}).selectOption('BOOKED');
  await expect(page.getByRole('button',{name:'Сохранить этап'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID записи'}).fill(appointment);
  await expect(page.getByRole('button',{name:'Сохранить этап'})).toBeEnabled();
});

test('CRM create survives lost response and 503 using one immutable request key',async({page})=>{
  const posts:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(path,route=>{
    const request=route.request();
    if(request.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:posts.length?[item('NEW')]:[]}),
    });
    posts.push(request.postDataJSON());
    expect(request.headers()['x-csrf-token']).toBe('synthetic-csrf');
    if(posts.length===1)return route.abort('failed');
    if(posts.length===2)return route.fulfill({status:503,body:'Unavailable'});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({replayed:true,...item('NEW')})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('UUID подтверждённой связи').fill(link);
  await page.getByRole('button',{name:'Создать обращение'}).click();
  const retry=page.getByRole('button',{name:'Повторить операцию CRM с тем же ключом'});
  await expect(retry).toBeVisible();
  await expect(page.getByRole('button',{name:'Создать обращение'})).toBeDisabled();
  await retry.click();
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await expect(retry).toHaveCount(0);
  expect(posts).toHaveLength(3);
  expect(posts[0]).toEqual(posts[1]);
  expect(posts[1]).toEqual(posts[2]);
});

test('CRM stage replay keeps exact payload after lost response and blocks parallel mutations',async({page})=>{
  let qualified=false;const posts:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}',
  }));
  await page.route(path,route=>{
    const request=route.request();
    if(request.method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[item(qualified?'QUALIFIED':'NEW',
        qualified?[event('QUALIFIED',null,null)]:[])]}),
    });
    posts.push(request.postDataJSON());
    if(posts.length===1){qualified=true;return route.abort('failed');}
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({replayed:true,...item('QUALIFIED',[event('QUALIFIED',null,null)])})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('Обращение').selectOption(lead);
  await page.getByRole('button',{name:'Сохранить этап'}).click();
  const retry=page.getByRole('button',{name:'Повторить операцию CRM с тем же ключом'});
  await expect(retry).toBeVisible();
  await expect(page.getByRole('button',{name:'Сохранить этап'})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Создать обращение'})).toBeDisabled();
  await expect(page.getByLabel('Обращение')).toBeDisabled();
  await retry.click();
  await expect(page.getByText('Текущий этап: Обработано.',{exact:false})).toBeVisible();
  await expect(retry).toHaveCount(0);
  expect(posts).toHaveLength(2);
  expect(posts[0]).toEqual(posts[1]);
});

test('CRM retry revocation clears state; CSRF failure before POST needs no replay',async({page})=>{
  let csrfCount=0;let postCount=0;
  await page.route('**/api/v1/auth/csrf',route=>{
    csrfCount++;
    return route.fulfill({status:csrfCount===1?503:200,
      contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'});
  });
  await page.route(path,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null}),
    });
    postCount++;
    if(postCount===1)return route.abort('failed');
    return route.fulfill({status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('UUID подтверждённой связи').fill(link);
  const create=page.getByRole('button',{name:'Создать обращение'});
  await create.click();
  await expect(page.getByRole('status')).toContainText('Запрос не отправлен');
  const retry=page.getByRole('button',{name:'Повторить операцию CRM с тем же ключом'});
  await expect(retry).toHaveCount(0);
  expect(postCount).toBe(0);
  await create.click();
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(retry).toHaveCount(0);
  expect(postCount).toBe(2);
});

test('CRM retry keeps the original key when refreshing CSRF fails',async({page})=>{
  let csrfCalls=0;const posts:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>{
    csrfCalls++;
    return route.fulfill({status:csrfCalls===2?503:200,
      contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'});
  });
  await page.route(path,route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null}),
    });
    posts.push(route.request().postDataJSON());
    if(posts.length===1)return route.abort('failed');
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({replayed:true,...item('NEW')})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('UUID подтверждённой связи').fill(link);
  await page.getByRole('button',{name:'Создать обращение'}).click();
  const retry=page.getByRole('button',{name:'Повторить операцию CRM с тем же ключом'});
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByText('Не удалось обновить CSRF',{exact:false})).toBeVisible();
  await expect(retry).toBeVisible();
  await expect(page.getByRole('button',{name:'Создать обращение'})).toBeDisabled();
  expect(posts).toHaveLength(1);
  await retry.click();
  await expect(retry).toHaveCount(0);
  expect(posts).toHaveLength(2);
  expect(posts[0]).toEqual(posts[1]);
});


test('CRM journal paginates 28 leads, opens later page and resets on refresh',async({page})=>{
  const leads=Array.from({length:28},(_,i)=>({
    ...item('NEW'),
    leadId:'aaaaaaaa-aaaa-4aaa-8aaa-'+String(i+1).padStart(12,'0'),
  }));
  const seen:string[]=[];
  await page.route(path,route=>{
    if(route.request().method()!=='GET')return route.abort();
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    seen.push(cursor??'FIRST');
    const part=cursor?leads.slice(25):leads.slice(0,25);
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:part,
        nextCursor:cursor?null:'a'.repeat(190)})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const journal=page.getByLabel('Страницы журнала CRM');
  await expect(page.getByLabel('Обращение').locator('option')).toHaveCount(26);
  await journal.getByRole('button',{name:'Следующая страница обращений'}).click();
  await expect(page.getByLabel('Обращение').locator('option')).toHaveCount(4);
  await page.getByLabel('Обращение').selectOption(leads[27].leadId);
  await expect(page.getByText('Текущий этап: Новое.',{exact:false})).toBeVisible();
  await journal.getByRole('button',{name:'Предыдущая страница обращений'}).click();
  await expect(page.getByLabel('Обращение').locator('option')).toHaveCount(26);
  await page.getByRole('button',{name:'Обновить список'}).click();
  await expect(journal.getByRole('button',{name:'Предыдущая страница обращений'}))
    .toHaveCount(0);
  expect(seen).toContain('a'.repeat(190));
  expect(seen.at(-1)).toBe('FIRST');
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('CRM journal refuses malformed cursor and clears previous lead after 403',async({page})=>{
  let invalid=false;let denied=false;
  await page.route(path,route=>{
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    return route.fulfill({status:denied?403:200,
      contentType:'application/json',body:denied?
        '{"code":"FORBIDDEN"}':JSON.stringify({
          syntheticOnly:true,items:[item('NEW')],
          nextCursor:invalid?'../../../bad':cursor?null:'page-2',
        })});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const journal=page.getByLabel('Страницы журнала CRM');
  await expect(page.getByLabel('Обращение')).toBeVisible();
  invalid=true;
  await page.getByRole('button',{name:'Обновить список'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить обращения');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
});
 
test('CRM journal resets expired signed cursor after server restart',async({page})=>{
  const seen:string[]=[];
  const signed='a'.repeat(190);
  let restarted=false;
  await page.route(path,route=>{
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    seen.push(cursor??'FIRST');
    if(cursor&&restarted)return route.fulfill({status:422,contentType:'application/json',
      body:'{"code":"INVALID_INPUT"}'});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:[item('NEW')],nextCursor:cursor?null:signed})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByLabel('Обращение')).toBeVisible();
  restarted=true;
  await page.getByLabel('Страницы журнала CRM')
    .getByRole('button',{name:'Следующая страница обращений'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить обращения');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
  await page.getByRole('button',{name:'Обновить список'}).click();
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await expect(page.getByRole('button',{name:'Предыдущая страница обращений'})).toHaveCount(0);
  expect(seen).toEqual(['FIRST',signed,'FIRST']);
});

test('CRM journal reauthorizes page request after initial successful page',async({page})=>{
  await page.route(path,route=>{
    const cursor=new URL(route.request().url()).searchParams.get('cursor');
    return route.fulfill({status:cursor?403:200,
      contentType:'application/json',body:cursor?
        '{"code":"FORBIDDEN"}':JSON.stringify({
          syntheticOnly:true,items:[item('NEW')],nextCursor:'next-page',
        })});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await expect(page.getByLabel('Обращение')).toBeVisible();
  await page.getByLabel('Страницы журнала CRM')
    .getByRole('button',{name:'Следующая страница обращений'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByLabel('Обращение')).toHaveCount(0);
});


test('CRM staff creates and cancels a closed-order survey with exact retry',async({page})=>{
  const order='88888888-8888-4888-8888-888888888888';
  const survey='99999999-9999-4999-8999-999999999999';
  let state:'NONE'|'OPEN'|'CANCELED'='NONE';let attempt:unknown=null;let lost=true;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}'}));
  await page.route(path,route=>{
    const req=route.request();
    if(req.url().endsWith('/survey')){
      if(req.method()==='POST'){
        const data=req.postDataJSON();
        if(data.action==='INVITE'){
          if(lost){lost=false;attempt=data;state='OPEN';return route.abort();}
          expect(data).toEqual(attempt);
        }else{expect(data.action).toBe('CANCEL');state='CANCELED';}
      }
      return route.fulfill({status:req.method()==='POST'&&state==='CANCELED'?201:200,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,sendAllowed:false,releaseAllowed:false,workOrderId:order,
          eligible:true,replayed:req.method()==='POST'&&state==='OPEN',
          survey:state==='NONE'?null:{id:survey,createdAt:'2030-01-01T10:00:00Z',expiresAt:'2030-01-31T10:00:00Z',
            status:state,rating:null,reasonCode:null,resolvedAt:state==='CANCELED'?'2030-01-02T10:00:00Z':null}})});
    }
    if(req.url().endsWith('/tasks'))return route.fulfill({status:200,contentType:'application/json',body:'{"syntheticOnly":true,"items":[]}'});
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,items:[item('BOOKED',[event('BOOKED',null,appointment)])]})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');await scope(page);
  await page.getByLabel('Обращение').selectOption(lead);
  const section=page.getByRole('region',{name:'Опрос после ремонта'});
  await section.getByRole('button',{name:'Открыть опрос'}).click();
  await section.getByRole('button',{name:'Создать опрос в кабинете'}).click();
  await expect(section.getByText('Результат неизвестен.',{exact:false})).toBeVisible();
  await section.getByRole('button',{name:'Повторить исходное действие'}).click();
  await expect(section.getByText('Ожидает ответа')).toBeVisible();
  await section.getByRole('button',{name:'Отменить опрос'}).click();
  await expect(section.getByText('Отменён',{exact:true})).toBeVisible();
  await expect(section.getByRole('button',{name:'Отменить опрос'})).toHaveCount(0);
});


test('terminal CRM deal starts a new cycle with old evidence unchanged',async({page})=>{
  const second='88888888-8888-4888-8888-888888888888';
  const old={...item('LOST',[event('LOST','TIMING',null)]),visitNumber:1};
  const fresh={...item('NEW'),leadId:second,visitNumber:2};
  let created=false;
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route(path,route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;
    if(!pathname.endsWith('/crm/leads'))return route.fulfill({
      status:200,contentType:'application/json',body:JSON.stringify({syntheticOnly:true,items:[],nextCursor:null})});
    if(req.method()==='GET')return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:created?[fresh,old]:[old],nextCursor:null})});
    expect(req.postDataJSON()).toMatchObject({customerLinkId:link,sourceCode:'WEB'});
    created=true;
    return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({...fresh,replayed:false})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const select=page.getByLabel('Обращение',{exact:true});
  await select.selectOption(lead);
  await expect(page.getByText('Цикл обращения №1.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Создать повторное обращение'}).click();
  await expect(select.locator('option',{hasText:'Обращение №2'})).toHaveCount(1);
  await select.selectOption(second);
  await expect(page.getByText('Цикл обращения №2.',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Создать повторное обращение'})).toHaveCount(0);
  await select.selectOption(lead);
  await expect(page.getByText('Цикл обращения №1.',{exact:false})).toBeVisible();
  await expect(page.getByText('Отказ · Время',{exact:false})).toBeVisible();
});


test('deal card shows linked primary evidence and separate journals, clears on revoke',async({page})=>{
  const order='99999999-9999-4999-8999-999999999999';
  const id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const date='2030-01-01T10:00:00Z';
  const deal={syntheticOnly:true,realMoney:false,releaseAllowed:false,leadId:lead,
    visitNumber:1,stage:'BOOKED',lineage:'LINKED',financeStatus:'VISIBLE',
    booking:{appointmentId:appointment,startsAt:date,endsAt:'2030-01-01T11:00:00Z',status:'RESCHEDULED'},
    workOrder:{workOrderId:order,steps:{services:'SELECTED',inspection:'RECORDED',
      intakeAct:'ACKNOWLEDGED',estimate:'APPROVED',execution:'FINISHED',quality:'PASS',technicalDocuments:'CLOSED_TECHNICAL'},
      documentEvents:[{id,action:'CLOSED_TECHNICAL',createdAt:date}]},
    finance:{currency:'RUB',approvedTotalMinor:'2500',
      demoClose:{paidMinor:'2500',refundedMinor:'0',dueMinor:'0',
        events:[{id,kind:'SIM_PAYMENT',amountMinor:'2500',createdAt:date}]},
      treasury:{advancedMinor:'0',paymentMinor:'1700',appliedMinor:'0',refundedAdvanceMinor:'0',
        refundedPaymentMinor:'0',settledMinor:'1700',dueMinor:'800',
        events:[{id,kind:'PAYMENT',amountMinor:'1700',createdAt:date}]}}};
  let mode='visible';
  await page.route(path,route=>{
    const pathname=new URL(route.request().url()).pathname;
    if(pathname.endsWith('/deal')){
      const payload=mode==='restricted'?{...deal,financeStatus:'RESTRICTED',finance:null,
        workOrder:{...deal.workOrder,steps:{...deal.workOrder.steps,technicalDocuments:'RESTRICTED'},documentEvents:null}}:
        mode==='malformed'?{...deal,finance:{...deal.finance,treasury:{...deal.finance.treasury,settledMinor:'2500'}}}:deal;
      return route.fulfill({status:mode==='denied'?403:200,contentType:'application/json',
        body:JSON.stringify(mode==='denied'?{code:'FORBIDDEN'}:payload)});
    }
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:pathname.endsWith('/crm/leads')?
        [{...item('BOOKED',[event('BOOKED',null,appointment)]),visitNumber:1}]:[],nextCursor:null})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('Обращение',{exact:true}).selectOption(lead);
  const card=page.getByRole('region',{name:'Карточка сделки CRM'});
  await card.getByRole('button',{name:'Загрузить карточку сделки'}).click();
  await expect(card).toContainText('Перенесена');
  await expect(card).toContainText(order);
  await expect(card).toContainText('Технически закрыто');
  await expect(card.getByRole('table',{name:'Имитация закрытия'})).toContainText('25,00');
  await expect(card.getByRole('table',{name:'Тестовое казначейство'})).toContainText('17,00');
  await expect(card).toContainText('8,00 ₽');
  const refresh=card.getByRole('button',{name:'Обновить карточку сделки'});
  mode='restricted';await refresh.click();
  await expect(card).toContainText('Финансовая история недоступна');
  await expect(card.getByRole('table')).toHaveCount(0);
  await expect(card.getByRole('heading',{name:'История документов'})).toHaveCount(0);
  mode='malformed';await refresh.click();
  await expect(card.getByRole('alert')).toContainText('Не удалось получить полную карточку');
  await expect(card.getByRole('table')).toHaveCount(0);
  mode='visible';await refresh.click();
  await expect(card.getByRole('table')).toHaveCount(2);
  mode='denied';await refresh.click();
  await expect(card.getByRole('alert')).toContainText('Доступ к сделке запрещён');
  await expect(card).not.toContainText(order);
  await expect(card.getByRole('table')).toHaveCount(0);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});


test('inbound contact keeps exact request after lost response and access denial',async({page})=>{
  const id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const record={interactionId:id,actorId:owner,ownerId:owner,sourceCode:'WEB',
    channelCode:'CALL',outcomeCode:'CALLBACK_REQUIRED',direction:'INBOUND_SIM',
    createdAt:'2030-01-01T10:00:00Z',callbackTaskId:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',callbackDueAt:''};
  const envelope={syntheticOnly:true,sendAllowed:false,leadId:lead,viewerId:owner};
  const posts:any[]=[];
  let denied=false;
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',
    body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route(path,route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;
    if(pathname.endsWith('/interactions')){
      if(req.method()==='POST'){
        posts.push(req.postDataJSON());
        expect(posts.at(-1)).toEqual(posts[0]);
        record.callbackDueAt=posts[0].callbackDueAt;
        expect(posts[0].expectedActorId).toBe(owner);
        if(posts.length===1)return route.abort('failed');
        if(posts.length===2){denied=true;return route.fulfill({status:403,body:'{}'});}
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify({...envelope,replayed:true,requestKey:posts[0].requestKey,item:record})});
      }
      return route.fulfill({status:denied?403:200,contentType:'application/json',
        body:JSON.stringify(denied?{}:{...envelope,items:posts.length?[record]:[],nextCursor:null})});
    }
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:pathname.endsWith('/crm/leads')?[item('NEW')]:[],nextCursor:null})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('Обращение',{exact:true}).selectOption(lead);
  const panel=page.getByRole('region',{name:'Журнал тестовых контактов'});
  await panel.getByRole('button',{name:'Загрузить журнал контактов'}).click();
  await panel.getByLabel('Результат входящего контакта').selectOption('CALLBACK_REQUIRED');
  const tomorrow=new Date(Date.now()+86400000);
  const local=new Date(tomorrow.getTime()-tomorrow.getTimezoneOffset()*60000).toISOString().slice(0,16);
  await panel.getByLabel('Срок обратного контакта').fill(local);
  await panel.getByRole('button',{name:'Записать входящий контакт'}).click();
  const retry=panel.getByRole('button',{name:'Повторить контакт с тем же ключом'});
  await expect(panel).toContainText('Ответ неизвестен');
  // Destroy the component and reload the page, then recover only after a new scoped GET.
  await page.reload();
  await scope(page);
  await page.getByLabel('Обращение',{exact:true}).selectOption(lead);
  await panel.getByRole('button',{name:'Загрузить журнал контактов'}).click();
  await expect(retry).toBeEnabled();
  await expect(panel.getByRole('button',{name:'Записать входящий контакт'})).toBeDisabled();
  await retry.click();
  await expect(panel.getByRole('alert')).toContainText('Доступ к журналу контактов запрещён');
  await expect(panel.getByRole('listitem')).toHaveCount(0);
  denied=false;
  await panel.getByRole('button',{name:'Обновить журнал контактов'}).click();
  await expect(retry).toBeEnabled();
  await expect(panel.getByRole('button',{name:'Записать входящий контакт'})).toBeDisabled();
  await retry.click();
  await expect(panel).toContainText('Входящий тестовый контакт записан');
  await expect(panel.getByRole('listitem')).toHaveCount(1);
  await expect(retry).toHaveCount(0);
  expect(posts).toHaveLength(3);
  await expect(panel).toContainText('Задача обратного контакта:');
  await panel.getByText('Аудит контакта').click();
  await expect(panel).toContainText(id);
  denied=true;
  await panel.getByRole('button',{name:'Обновить журнал контактов'}).click();
  await expect(panel.getByRole('alert')).toBeVisible();
  await expect(panel).not.toContainText(id);
});


test('expired callback pending clears only for exact terminal rejection',async({page})=>{
  const envelope={syntheticOnly:true,sendAllowed:false,leadId:lead,viewerId:owner};
  const posts:any[]=[];
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',
    body:'{"csrfToken":"synthetic-csrf"}'}));
  await page.route(path,route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;
    if(pathname.endsWith('/interactions')){
      if(req.method()==='POST'){
        const body=req.postDataJSON();posts.push(body);
        if(posts.length===1)return route.abort('failed');
        expect(body).toEqual(posts[0]);
        return route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({
          ...envelope,code:'CALLBACK_DEADLINE_EXPIRED',callbackDueAt:body.callbackDueAt,
          requestKey:posts.length===2?'00000000-0000-4000-8000-000000000000':body.requestKey})});
      }
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({...envelope,items:[],nextCursor:null})});
    }
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,items:pathname.endsWith('/crm/leads')?[item('NEW')]:[],nextCursor:null})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  await page.getByLabel('Обращение',{exact:true}).selectOption(lead);
  const panel=page.getByRole('region',{name:'Журнал тестовых контактов'});
  await panel.getByRole('button',{name:'Загрузить журнал контактов'}).click();
  await panel.getByLabel('Результат входящего контакта').selectOption('CALLBACK_REQUIRED');
  const tomorrow=new Date(Date.now()+86400000);
  await panel.getByLabel('Срок обратного контакта').fill(
    new Date(tomorrow.getTime()-tomorrow.getTimezoneOffset()*60000).toISOString().slice(0,16));
  const create=panel.getByRole('button',{name:'Записать входящий контакт'});
  const retry=panel.getByRole('button',{name:'Повторить контакт с тем же ключом'});
  await create.click();
  await expect(panel).toContainText('Ответ неизвестен');
  await retry.click();
  await expect(retry).toBeEnabled();
  await expect(create).toBeDisabled();
  await retry.click();
  await expect(panel).toContainText('Сервер подтвердил отсутствие записи');
  await expect(retry).toHaveCount(0);
  await expect(create).toBeEnabled();
  expect(posts).toHaveLength(3);
});


test('search finds a lead outside the current journal page and rechecks detail',async({page})=>{
  const older='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  let denied=false,detailReads=0;
  await page.route('**/crm/lead-search?*',r=>{
    const u=new URL(r.request().url());
    expect(u.searchParams.get('customerLinkId')).toBe(link);
    expect(u.searchParams.get('sourceCode')).toBe('WEB');
    expect(u.searchParams.get('stageCode')).toBe('LOST');
    return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,organizationId:org,locationId:loc,customerLinkId:link,sourceCode:'WEB',stageCode:'LOST',
      items:[{leadId:older,customerLinkId:link,ownerId:owner,sourceCode:'WEB',status:'LOST',
        visitNumber:1,createdAt:'2030-01-01T10:00:00Z'}],nextCursor:null})});
  });
  await page.route(path,r=>{
    const pathname=new URL(r.request().url()).pathname;
    if(pathname.endsWith('/leads/'+older)){
      detailReads++;
      return r.fulfill({status:denied?403:200,contentType:'application/json',body:JSON.stringify(denied?{}:
        {...item('LOST',[event('LOST','TIMING',null)]),leadId:older,visitNumber:1})});
    }
    return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,items:pathname.endsWith('/crm/leads')?[item('NEW')]:[],nextCursor:null})});
  });
  await page.goto('/staff/auto-crm-leads');
  await scope(page);
  const panel=page.getByRole('region',{name:'Поиск обращений CRM'});
  await panel.getByLabel('UUID связи для поиска').fill(link);
  await panel.getByLabel('Источник поиска').selectOption('WEB');
  await panel.getByLabel('Этап поиска').selectOption('LOST');
  await panel.getByRole('button',{name:'Найти обращения'}).click();
  await expect(panel.getByRole('table')).toContainText('Отказ');
  await panel.getByRole('button',{name:'Открыть найденную сделку'}).click();
  await expect(page.getByLabel('Обращение',{exact:true})).toHaveValue(older);
  expect(detailReads).toBe(1);
  denied=true;
  await panel.getByRole('button',{name:'Найти обращения'}).click();
  await panel.getByRole('button',{name:'Открыть найденную сделку'}).click();
  await expect(page.getByText('Доступ запрещён или связь с автомобилем отозвана.')).toBeVisible();
  await expect(panel).toHaveCount(0);
  expect(detailReads).toBe(2);
});


test('CRM owner and period export validates digest and revocation',async({page})=>{
  const {createHash}=await import('node:crypto');
  const bytes=Buffer.from('\ufeffleadId,customerLinkId,ownerId,sourceCode,status,createdAt,visitNumber\r\n'+
    [lead,link,owner,'WEB','NEW','2030-01-01T10:00:00Z','1'].join(',')+'\r\n');
  let denied=false,corrupt=true;
  await page.route('**/crm/lead-search*',r=>{
    const u=new URL(r.request().url());
    expect(u.searchParams.get('ownerId')).toBe(owner);
    expect(u.searchParams.get('fromDay')).toBe('2030-01-01');
    expect(u.searchParams.get('toDay')).toBe('2030-01-02');
    if(u.pathname.endsWith('/export'))return r.fulfill({status:denied?403:200,body:bytes,
      headers:{'Content-Type':'text/csv','X-CRM-SHA256':corrupt?'0'.repeat(64):createHash('sha256').update(bytes).digest('hex'),
        'X-CRM-Row-Count':'1'}});
    return r.fulfill({json:{syntheticOnly:true,organizationId:org,locationId:loc,customerLinkId:null,
      sourceCode:'ALL',stageCode:'ALL',ownerId:owner,fromDay:'2030-01-01',toDay:'2030-01-02',timeZone:'Europe/Berlin',
      items:[{leadId:lead,customerLinkId:link,ownerId:owner,sourceCode:'WEB',status:'NEW',visitNumber:1,createdAt:'2030-01-01T10:00:00Z'}],nextCursor:null}});
  });
  await page.route(path,r=>r.fulfill({json:{syntheticOnly:true,items:new URL(r.request().url()).pathname.endsWith('/crm/leads')?[item('NEW')]:[],nextCursor:null}}));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-crm-leads');await scope(page);
  const panel=page.getByRole('region',{name:'Поиск обращений CRM'});
  await panel.getByLabel('UUID ответственного').fill(owner);
  await panel.getByLabel('Создано с',{exact:true}).fill('2030-01-01');
  await expect(panel.getByRole('button',{name:'Найти обращения'})).toBeDisabled();
  await panel.getByLabel('Создано по',{exact:true}).fill('2030-01-02');
  await panel.getByRole('button',{name:'Найти обращения'}).click();
  await expect(panel).toContainText('Europe/Berlin');
  const button=panel.getByRole('button',{name:'Скачать все найденные обращения (CSV)'});
  await button.click();await expect(panel).toContainText('Выгрузка не подтверждена');
  corrupt=false;
  const download=page.waitForEvent('download');await button.click();
  expect((await download).suggestedFilename()).toBe('synthetic-crm-leads.csv');
  await expect(panel).toContainText('Выгружено обращений: 1');
  denied=true;await button.click();
  await expect(panel).toHaveCount(0);
});
