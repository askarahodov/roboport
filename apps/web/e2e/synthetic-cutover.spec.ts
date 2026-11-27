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



test('подготовка XLSX и mapping: канонические файлы до preview и commit',async({page})=>{
  const canonical:Record<string,string>={
    'vehicles.csv':'record_type,source_id,organization_id,location_id,vin,make,model,source_system\nVEHICLE,SYN-1,'+org+','+loc+',ABCDEF12345678901,SYNTHETIC,TEST CAR,DEMO\n',
    'services.csv':'record_type,source_system,source_id,code,title,duration_minutes\nSERVICE,DEMO,SYN-1,SYN-WORK,SYNTHETIC WORK,60\n',
    'inventory.csv':'record_type,source_id,organization_id,location_id,article,quantity\nSKU,SYN-1,'+org+','+loc+',SYN-PART,0\nSTOCK,SYN-2,'+org+','+loc+',SYN-PART,3\n',
  };
  const files=Object.fromEntries(Object.entries(canonical).map(([name,value])=>[name,{
    base64:Buffer.from(value).toString('base64'),sha256:createHash('sha256').update(value).digest('hex'),
  }]));
  let prepared=false;let corrupt=true;
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({json:{csrfToken:'test'}}));
  await page.route('**/cutover/*',r=>{
    const operation=r.request().url().split('/').pop();
    const body=r.request().postDataJSON();
    if(operation==='prepare'){
      expect(body.files['vehicles.csv'].format).toBe('XLSX');
      expect(body.files['vehicles.csv'].mapping.F0).toBe('record_type');
      prepared=true;
      return r.fulfill({json:{ok:true,syntheticOnly:true,productionApproved:false,
        bundleSha256:hash,counts,batchIds,mode:'PREPARE',writes:0,
        files:corrupt?{...files,'vehicles.csv':{...files['vehicles.csv'],sha256:'0'.repeat(64)}}:files}});
    }
    expect(operation).toBe('preview');expect(prepared).toBe(true);
    expect(body.files).toEqual(files);
    return r.fulfill({json:{ok:true,syntheticOnly:true,productionApproved:false,
      bundleSha256:hash,counts,batchIds,mode:'PREVIEW',writes:0}});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-cutover');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала').fill(loc);
  await page.locator('input[type=file]').nth(0).setInputFiles({name:'vehicle.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from('workbook fixture')});
  for(const [i,n] of ['services.csv','inventory.csv'].entries()){
    await page.locator('input[type=file]').nth(i+1).setInputFiles({name:n,mimeType:'text/csv',buffer:Buffer.from(canonical[n])});
  }
  await page.getByLabel('Колонки источника для vehicles.csv').fill('F0,F1,F2,F3,F4,F5,F6,F7');
  await page.getByLabel('Подтверждаю, что все файлы искусственные, без реальных данных').check();
  const preview=page.getByRole('button',{name:'Предварительная проверка'});
  await expect(preview).toBeDisabled();
  await page.getByRole('button',{name:'Подготовить CSV/XLSX'}).click();
  await expect(page.getByText('Не удалось подтвердить результат.',{exact:false})).toBeVisible();
  await expect(preview).toBeDisabled();
  corrupt=false;
  await page.getByRole('button',{name:'Подготовить CSV/XLSX'}).click();
  await expect(page.getByText('Комплект подготовлен.',{exact:false})).toBeVisible();
  await expect(preview).toBeEnabled();
  await preview.click();
  await expect(page.getByRole('heading',{name:'Проверенный комплект'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Импортировать комплект'})).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
