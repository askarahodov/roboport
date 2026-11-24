import {test,expect} from '@playwright/test';
import {createHash} from 'node:crypto';

const org='11111111-1111-4111-8111-111111111111',loc='22222222-2222-4222-8222-222222222222';
const hash='a'.repeat(64),manifest='b'.repeat(64);
const counts={vehicles:1,services:1,stockRows:2,skus:1,receipts:1};
const batchIds={'vehicles.csv':org,'services.csv':loc,'inventory.csv':'33333333-3333-4333-8333-333333333333'};

test('репетиция импорта: подтверждение, повторная сверка, архив и отзыв',async({page})=>{
  let prior:unknown=null;let revoke=false;let corrupt=false;let commits=0;
  const zip=Buffer.from('synthetic mock ZIP bytes');
  const digest=createHash('sha256').update(zip).digest('hex');
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({status:200,contentType:'application/json',
    body:'{"csrfToken":"test-csrf"}'}));
  await page.route('**/cutover/*',route=>{
    const req=route.request(),operation=req.url().split('/').pop()!;
    expect(req.headers()['x-csrf-token']).toBe('test-csrf');
    const body=req.postDataJSON();
    expect(body.syntheticOnly).toBe(true);
    if(operation==='preview'){prior=body.files;expect(body.bundleSha256).toBeUndefined();}
    else{expect(body.files).toEqual(prior);expect(body.bundleSha256).toBe(hash);}
    if(revoke)return route.fulfill({status:403,body:'{}'});
    if(operation==='export')return route.fulfill({status:200,body:zip,headers:{
      'Content-Type':'application/zip','X-Cutover-SHA256':corrupt?'0'.repeat(64):digest,
      'X-Cutover-Manifest-SHA256':manifest}});
    if(operation==='commit')commits++;
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      ok:true,syntheticOnly:true,productionApproved:false,bundleSha256:hash,counts,batchIds,
      mode:operation.toUpperCase(),writes:operation==='commit'&&commits===1?8:0,replayed:commits>1,
    })});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-cutover');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.getByRole('button',{name:'Создать искусственный комплект'}).click();
  await expect(page.getByRole('button',{name:'Предварительная проверка'})).toBeDisabled();
  await page.getByLabel('Подтверждаю, что все файлы искусственные, без реальных данных').check();
  await page.getByRole('button',{name:'Предварительная проверка'}).click();
  await expect(page.getByText('Структура проверена. Записей в БД нет.')).toBeVisible();
  const commit=page.getByRole('button',{name:'Импортировать комплект'});
  await expect(commit).toBeDisabled();
  await page.getByLabel('Подтверждаю атомарную запись этого комплекта в тестовую БД').check();
  await commit.click();
  await expect(page.getByText('Импорт завершён атомарно.')).toBeVisible();
  await page.getByLabel('Подтверждаю атомарную запись этого комплекта в тестовую БД').check();
  await commit.click();
  await expect(page.getByText('Подтверждён повтор без новых записей.')).toBeVisible();
  await page.getByRole('button',{name:'Сверить с БД'}).click();
  await expect(page.getByText('Исходники, staging и активированные объекты сверены.')).toBeVisible();
  for(const width of [390,1280,1440]){
    await page.setViewportSize({width,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    for(const input of await page.locator('input[type=file]').all()){
      const bounds=await input.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(width);
    }
  }
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Скачать проверенный ZIP'}).click();
  expect((await download).suggestedFilename()).toBe('synthetic-cutover.zip');
  await expect(page.getByText(manifest,{exact:false})).toBeVisible();
  corrupt=true;
  await page.getByRole('button',{name:'Скачать проверенный ZIP'}).click();
  await expect(page.getByText('Не удалось подтвердить результат.',{exact:false})).toBeVisible();
  await expect(page.getByText(manifest,{exact:false})).toHaveCount(0);
  revoke=true;
  await page.getByRole('button',{name:'Сверить с БД'}).click();
  await expect(page.getByText('Доступ запрещён или тестовый импорт отключён.',{exact:false})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Проверенный комплект'})).toHaveCount(0);
});
