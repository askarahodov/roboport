import {test,expect} from '@playwright/test';
const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const lot='33333333-3333-4333-8333-333333333333';
const doc='44444444-4444-4444-8444-444444444444';
const actor='55555555-5555-4555-8555-555555555555';
const reviewer='66666666-6666-4666-8666-666666666666';
const event='77777777-7777-4777-8777-777777777777';
const debit='88888888-8888-4888-8888-888888888888';
const stamp='2030-01-01T00:00:00Z';
const serial1='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',serial2='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

test('writeoff prepares immutable act, independent approval and exact retry',async({page})=>{
  let made=false,approved=false,manager=false,denied=false,lost=true;
  const requests:{key:string;body:string}[]=[];
  const entry={lotId:lot,article:'SYN-PART',batchCode:'BATCH',binCode:'BIN'};
  const snapshot=()=>({syntheticOnly:true,organizationId:org,locationId:loc,canDraft:!manager,
    lots:manager?[]:[{...entry,available:approved?'3':'5'}],nextLotCursor:null,nextCursor:null,
    items:made?[{...entry,documentId:doc,quantity:'2',reasonCode:'DAMAGED',note:'Синтетическое повреждение',
      createdBy:actor,createdAt:stamp,serialIds:[serial1,serial2],status:approved?'APPROVED':'PENDING',
      canReview:manager&&!approved,canCancel:!manager&&!approved,
      decision:approved?{id:reviewer,note:'Осмотрено управляющим',actorId:reviewer,createdAt:stamp,
        dispositionId:doc,stockEventId:event,lotDebitId:debit}:null}]:[]});
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.route('**/warehouse/writeoffs**',route=>{
    const req=route.request(),url=new URL(req.url());
    if(denied)return route.fulfill({status:403,contentType:'application/json',body:'{}'});
    if(req.method()==='GET')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(snapshot())});
    expect(req.headers()['x-csrf-token']).toBe('csrf');
    if(url.pathname.endsWith('/writeoffs')){
      expect(req.postDataJSON()).toEqual({locationId:loc,lotId:lot,quantity:2,reasonCode:'DAMAGED',note:'Синтетическое повреждение',serialIds:[serial1,serial2]});
      made=true;
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({syntheticOnly:true,documentId:doc,replayed:false})});
    }
    expect(url.pathname.endsWith('/'+doc+'/decisions')).toBe(true);
    expect(req.postDataJSON()).toEqual({locationId:loc,action:'APPROVE',note:'Осмотрено управляющим'});
    requests.push({key:req.headers()['idempotency-key'],body:req.postData()??''});approved=true;
    if(lost){lost=false;return route.abort('failed');}
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({syntheticOnly:true,decisionId:reviewer,replayed:true})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-writeoffs');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  const load=page.getByRole('button',{name:'Загрузить партии и акты'});
  await load.click();
  await page.getByLabel('Партия для списания').selectOption(lot);
  await page.getByLabel('Количество').fill('2');
  await page.getByLabel('Обоснование списания').fill('Синтетическое повреждение');
  await page.getByLabel('UUID серий для списания',{exact:false}).fill(serial1);
  await expect(page.getByRole('button',{name:'Создать акт списания'})).toBeDisabled();
  await page.getByLabel('UUID серий для списания',{exact:false}).fill(serial2+', '+serial1);
  await page.getByRole('button',{name:'Создать акт списания'}).click();
  await expect(page.getByText('Остатки не изменены.',{exact:false})).toBeVisible();
  await load.click();
  await expect(page.getByText('SYN-PART · 2 ед. · Ожидает решения',{exact:true})).toBeVisible();
  await expect(page.getByText('Серии акта: '+serial1+', '+serial2)).toBeVisible();
  await expect(page.getByRole('button',{name:'Утвердить акт'})).toHaveCount(0);
  manager=true;
  await load.click();
  await expect(page.getByRole('button',{name:'Создать акт списания'})).toHaveCount(0);
  await page.getByRole('button',{name:'Утвердить акт'}).click();
  await page.getByLabel('Обоснование решения').fill('Осмотрено управляющим');
  await page.getByRole('button',{name:'Подтвердить решение'}).click();
  await expect(page.getByText('Результат неизвестен.',{exact:false})).toBeVisible();
  await expect(page.getByLabel('UUID филиала')).toBeDisabled();
  await page.getByRole('button',{name:'Повторить исходную операцию'}).click();
  await expect(page.getByText('Решение записано.',{exact:false})).toBeVisible();
  expect(requests).toHaveLength(2);expect(requests[0]).toEqual(requests[1]);
  await load.click();
  await expect(page.getByText('SYN-PART · 2 ед. · Утверждён',{exact:true})).toBeVisible();
  await page.getByText('Решение и проводки',{exact:true}).click();
  await expect(page.getByText('Списание '+event+' · снятие размещения '+debit)).toBeVisible();
  denied=true;
  await load.click();
  await expect(page.getByRole('alert')).toContainText('Доступ к списанию запрещён');
  await expect(page.getByRole('region',{name:'Журнал списания'})).toHaveCount(0);
});
