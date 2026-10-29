import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const src='33333333-3333-4333-8333-333333333333';
const dst='44444444-4444-4444-8444-444444444444';
const doc='55555555-5555-4555-8555-555555555555';
const approval='66666666-6666-4666-8666-666666666666';

async function setup(page:any){
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.goto('/staff/warehouse-reclassification');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID исходной партии'}).fill(src);
  await page.getByRole('textbox',{name:'UUID целевой партии'}).fill(dst);
  await page.getByRole('spinbutton',{name:'Количество'}).fill('2');
  await page.getByRole('textbox',{name:'Причина пересортицы'}).fill('Ошибка классификации');
}

test('reclassification requires maker/checker and keeps exact ledger scope',async({page})=>{
  const requests:{path:string;key:string;body:any}[]=[];
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/reclassifications**',route=>{
    const req=route.request();
    expect(req.headers()['x-csrf-token']).toBe('csrf');
    requests.push({path:new URL(req.url()).pathname,
      key:req.headers()['idempotency-key'],body:req.postDataJSON()});
    const accept=req.url().endsWith('/approvals');
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify(accept?{approvalId:approval}:{documentId:doc})});
  });
  await setup(page);
  await page.getByRole('button',{name:'Создать акт пересортицы'}).click();
  await expect(page.getByRole('status')).toContainText('Остатки ещё не изменены');
  await expect(page.getByRole('textbox',{name:'UUID акта'})).toHaveValue(doc);
  await page.getByRole('button',{name:'Утвердить пересортицу'}).click();
  await expect(page.getByRole('status')).toContainText('обновлены атомарно');
  expect(requests).toHaveLength(2);
  expect(requests[0].body).toEqual({locationId:loc,sourceLotId:src,
    destinationLotId:dst,quantity:2,reason:'Ошибка классификации'});
  expect(requests[1].body).toEqual({locationId:loc,documentId:doc});
  expect(requests[0].key).not.toBe(requests[1].key);
  expect(requests[0].path).toBe('/api/v1/dev/auto/organizations/'+org+'/warehouse/reclassifications');
  expect(requests[1].path).toBe('/api/v1/dev/auto/organizations/'+org+'/warehouse/reclassifications/approvals');
});

test('uncertain approval holds key despite branch change',async({page})=>{
  const keys:string[]=[];
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/v1/dev/auto/organizations/**/warehouse/reclassifications**',async route=>{
    const req=route.request();
    if(!req.url().endsWith('/approvals')){
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({documentId:doc})});
    }
    keys.push(req.headers()['idempotency-key']);
    if(keys.length===1){await gate;return route.fulfill({status:503,body:'error'});}
    return route.fulfill({status:201,contentType:'application/json',
      body:JSON.stringify({approvalId:approval})});
  });
  await setup(page);
  await page.getByRole('button',{name:'Создать акт пересортицы'}).click();
  await expect(page.getByRole('textbox',{name:'UUID акта'})).toHaveValue(doc);
  const sent=page.waitForRequest('**/warehouse/reclassifications/approvals');
  await page.getByRole('button',{name:'Утвердить пересортицу'}).click();
  await sent;
  await page.getByRole('textbox',{name:'UUID филиала'}).fill('88888888-8888-4888-8888-888888888888');
  release();
  await expect(page.getByRole('alert')).toContainText('Результат запроса не определён');
  await expect(page.getByRole('button',{name:'Повторить с прежним ключом'})).toBeDisabled();
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByRole('status')).toContainText('обновлены атомарно');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
