import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const a='33333333-3333-4333-8333-333333333333';
const b='44444444-4444-4444-8444-444444444444';
const preview='55555555-5555-4555-8555-555555555555';
const commit='66666666-6666-4666-8666-666666666666';
const root='/api/v1/dev/auto/organizations/'+org+'/warehouse/catalog';
const digest='a'.repeat(64);

const items=[
  {skuId:a,article:'SYNTH-A',category:'PARTS',uom:'PCS',barcode:null,brand:'SYNTH',
   version:1,purchaseMinor:1200,saleMinor:2300,
   purchaseHidden:false,saleHidden:false,mergedInto:null},
  {skuId:b,article:'SYNTH-B',category:'PARTS',uom:'PCS',barcode:null,brand:'SYNTH',
   version:1,purchaseMinor:1400,saleMinor:2400,
   purchaseHidden:false,saleHidden:false,mergedInto:null},
];
async function visit(page:Page){
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.goto('/staff/warehouse-catalog');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
}
function json(route:any,obj:unknown,status=200){
  return route.fulfill({status,contentType:'application/json',
    body:JSON.stringify(obj)});
}

test('desktop full catalog → price → analog → logical merge → XLSX preview/commit',async({page})=>{
  await page.setViewportSize({width:1280,height:800});
  const posted:Array<{path:string;key:string;body:any}>=[];
  await page.route('**/warehouse/catalog**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()==='GET'&&path===root)
      return json(route,{syntheticOnly:true,items,nextSkuId:null});
    if(req.method()==='GET'&&path.endsWith('/export'))
      return route.fulfill({status:403,body:'{"code":"FORBIDDEN"}'});
    expect(req.headers()['x-csrf-token']).toBe('csrf');
    expect(req.headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/);
    const body=path.endsWith('/preview')?'binary-xlsx':req.postDataJSON();
    posted.push({path,key:req.headers()['idempotency-key'],body});
    if(path.endsWith('/preview'))
      return json(route,{previewId:preview,sha256:digest,syntheticOnly:true,
        replayed:false,items:[{article:'SYNTH-A',skuId:a,version:1},
          {article:'SYNTH-C',skuId:null,version:0}]},201);
    if(path.endsWith('/commit'))
      return json(route,{commitId:commit,rowsWritten:2,syntheticOnly:true,
        replayed:false},201);
    if(path.endsWith('/skus')||path.endsWith('/prices'))
      return json(route,{batchId:commit,items:[{skuId:a,version:2}],
        syntheticOnly:true,replayed:false},201);
    if(path.endsWith('/analogs'))
      return json(route,{analogId:commit,syntheticOnly:true,replayed:false},201);
    if(path.endsWith('/merges'))
      return json(route,{mergeId:commit,historicalFksRewritten:false,
        syntheticOnly:true,replayed:false},201);
    throw Error('unexpected '+path);
  });
  await visit(page);
  await page.getByRole('button',{name:'Загрузить номенклатуру'}).click();
  await expect(page.getByRole('table').getByText('SYNTH-A',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Создать SKU и исходную версию цены'}).click();
  await expect(page.getByText('Операция записана в неизменяемый журнал.')).toBeVisible();
  await page.getByRole('textbox',{name:'Строки обновления цены'}).fill(a+';1;1280;2600');
  await page.getByRole('button',{name:'Сохранить пакет версий цен'}).click();
  await expect.poll(()=>posted.find(p=>p.path.endsWith('/prices'))?.body).toEqual({
    locationId:loc,entries:[{skuId:a,expectedVersion:1,
      purchaseMinor:1280,saleMinor:2600}]});
  await page.getByRole('button',{name:'Загрузить номенклатуру'}).click();
  await page.getByLabel('Исходный SKU').selectOption(a);
  await page.getByLabel('Целевой SKU').selectOption(b);
  await page.getByRole('button',{name:'Сохранить аналог (не OEM-подтверждение)'}).click();
  await expect.poll(()=>posted.find(p=>p.path.endsWith('/analogs'))?.body).toMatchObject({
    locationId:loc,leftSkuId:a,rightSkuId:b});
  await page.getByLabel('Подтверждаю только логическое объединение').check();
  await page.getByRole('button',{name:'Объединить SKU с сохранением истории'}).click();
  await expect.poll(()=>posted.find(p=>p.path.endsWith('/merges'))?.body).toEqual({
    locationId:loc,sourceSkuId:a,targetSkuId:b});
  await page.locator('input[type=file]').setInputFiles({
    name:'synthetic.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    buffer:Buffer.from('synthetic fixture')});
  await page.getByRole('button',{name:'Проверить XLSX без записи в каталог'}).click();
  await expect(page.getByText('Предпросмотр: 2 строк')).toBeVisible();
  await page.getByRole('button',{name:'Подтвердить весь XLSX атомарно'}).click();
  await expect.poll(()=>posted.find(p=>p.path.endsWith('/commit'))?.body).toEqual({
    locationId:loc,previewId:preview,sha256:digest});
  expect(posted.map(p=>p.key).length).toBe(new Set(posted.map(p=>p.key)).size);
});

test('mobile unknown POST result retains exact idempotency key and branch context',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({colorScheme:'dark'});
  const saved:Array<{key:string;payload:string|null}>=[];
  await page.route('**/warehouse/catalog/skus',route=>{
    const req=route.request();
    saved.push({key:req.headers()['idempotency-key'],payload:req.postData()});
    if(saved.length===1)return route.fulfill({status:503,body:'ambiguous'});
    return json(route,{batchId:commit,items:[{skuId:a,version:1}],
      syntheticOnly:true,replayed:false},201);
  });
  await visit(page);
  await page.getByRole('button',{name:'Создать SKU и исходную версию цены'}).click();
  await expect(page.getByRole('button',{name:'Повторить с прежним ключом'})).toBeVisible();
  await page.getByRole('button',{name:'Повторить с прежним ключом'}).click();
  await expect(page.getByText('Операция записана в неизменяемый журнал.')).toBeVisible();
  expect(saved).toHaveLength(2);
  expect(saved[0]).toEqual(saved[1]);
});

test('403 list denies and clears prior warehouse prices',async({page})=>{
  let denied=false;
  await page.route('**/warehouse/catalog?*',route=>denied?
    json(route,{code:'FORBIDDEN'},403):
    json(route,{syntheticOnly:true,items,nextSkuId:null}));
  await visit(page);
  await page.getByRole('button',{name:'Загрузить номенклатуру'}).click();
  await expect(page.getByRole('table').getByText('SYNTH-A',{exact:true})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Загрузить номенклатуру'}).click();
  await expect(page.getByRole('table').getByText('SYNTH-A',{exact:true})).toHaveCount(0);
  await expect(page.getByText('403 — нет активных прав')).toBeVisible();
});


test('changing branch while GET is inflight restores enabled reload and clears stale rows',async({page})=>{
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  let started=false;
  await page.route('**/warehouse/catalog?*',async route=>{
    if(!started){
      started=true;
      await gate;
      return json(route,{syntheticOnly:true,items,nextSkuId:null});
    }
    return json(route,{syntheticOnly:true,items:[],nextSkuId:null});
  });
  await visit(page);
  await page.getByRole('button',{name:'Загрузить номенклатуру'}).click();
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(
    '77777777-7777-4777-8777-777777777777');
  await expect(page.getByRole('button',{name:'Загрузить номенклатуру'})).toBeEnabled();
  release();
  await page.getByRole('button',{name:'Загрузить номенклатуру'}).click();
  await expect(page.getByRole('table').getByText('SYNTH-A',{exact:true})).toHaveCount(0);
});
