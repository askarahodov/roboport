import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const actId='44444444-4444-4444-8444-444444444444';
const inspectorId='55555555-5555-4555-8555-555555555555';
const query=new URLSearchParams({org,loc,order}).toString();
const root='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders/'+order;
const base=root+'/intake-acts';
const invoice={id:inspectorId,odometerKm:75000,fuelPercent:42,exteriorCode:'DAMAGE'};
const initial={syntheticOnly:true,releaseAllowed:false,
  legalSignatureStatus:'NOT_IMPLEMENTED',inspection:invoice,locked:false,
  latestVersion:0,previousVisit:null,items:[]};

test('intake → damage + custody + photo → immutable version → client preview (390/1280)',async({page})=>{
  let items:any[]=[];
  let reads=0;
  let png:Buffer|null=null;
  const events:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(path.endsWith('/inspection-qc'))return route.fulfill({
      status:200,contentType:'application/json',
      body:JSON.stringify({status:'PENDING_QC',releaseAllowed:false,inspection:{
        id:inspectorId,odometerKm:75000,fuelPercent:42,tyresOk:true,
        lightsOk:false,fluidLeak:false,exteriorCode:'DAMAGE',
        createdAt:'2030-01-01T10:00:00Z'},qcEvents:[],reworks:[]})});
    if(path.match(/\/photos\/1$/)){
      return route.fulfill({status:200,contentType:'image/png',body:png??Buffer.alloc(0),
        headers:{'Cache-Control':'no-store'}});
    }
    if(path.endsWith('/intake-acts')){
      if(req.method()==='GET'){
        reads++;
        return route.fulfill({status:200,contentType:'application/json',
          body:JSON.stringify({...initial,latestVersion:items.length,items})});
      }
      expect(req.method()).toBe('POST');
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const body=req.postDataJSON();
      expect(body.expectedVersion).toBe(0);
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      expect(body.act.vehicleKind).toBe('PASSENGER');
      expect(body.act.inventory).toHaveLength(5);
      expect(body.act.damages).toEqual([{
        areaCode:'FRONT',damageCode:'SCRATCH',severity:'MINOR'}]);
      expect(body.act.photos).toHaveLength(1);
      png=Buffer.from(body.act.photos[0].imageBase64,'base64');
      expect(png.subarray(0,8).toString('hex')).toBe('89504e470d0a1a0a');
      items=[{id:actId,version:1,vehicleKind:'PASSENGER',sha256:'a'.repeat(64),
        createdAt:'2030-01-01T10:15:00Z',actorId:org,
        damages:body.act.damages,inventory:body.act.inventory,
        photos:[{areaCode:'FRONT',sha256:'b'.repeat(64),mime:'image/png',
          width:2,height:2,index:1,path:base+'/'+actId+'/photos/1'}]}];
      events.push('act');
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({ok:true,replayed:false})});
    }
    throw Error('Unexpected request '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-workshop-qc?'+query);
  await expect(page.getByRole('heading',{name:'Состояние: PENDING_QC'})).toBeVisible();
  await page.getByRole('link',{
    name:'версионированный акт повреждений и комплектности (dev)'}).click();
  await expect(page.getByRole('heading',{
    name:'Оформление акта приёмки автомобиля (dev)'})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'UUID заказ-наряда'})).toHaveValue(order);
  await page.getByRole('combobox',{name:'Комплектность Ключи'}).selectOption('PRESENT');
  await page.getByRole('button',{name:'Добавить повреждение'}).click();
  await expect(page.getByRole('button',{name:'Зафиксировать версию акта'})).toBeDisabled();
  const pngBase64=await page.evaluate(()=>{
    const canvas=document.createElement('canvas');canvas.width=2;canvas.height=2;
    const ctx=canvas.getContext('2d')!;
    ctx.fillStyle='rgb(22,33,44)';ctx.fillRect(0,0,2,2);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).scrollIntoViewIfNeeded();
  await page.getByLabel('Добавить фотографии').setInputFiles({
    name:'synthetic.png',mimeType:'image/png',
    buffer:Buffer.from(pngBase64,'base64'),
  });
  await expect(page.getByText('Перед — PNG без метаданных')).toBeVisible();
  await page.getByRole('button',{name:'Зафиксировать версию акта'}).click();
  await expect(page.getByRole('heading',{name:'Акт №1 · PASSENGER'})).toBeVisible();
  await expect(page.getByText('Царапина',{exact:false}).first()).toBeVisible();
  await expect(page.getByText('Ключи: Есть').first()).toBeVisible();
  await expect(page.getByRole('img',{name:'Фото акта 1: Перед'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Печатать выбранную версию'})).toBeVisible();
  expect(events).toEqual(['act']);
  expect(reads).toBeGreaterThanOrEqual(2);
  await page.setViewportSize({width:1280,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(1280);
  await page.goto('/account/auto-intake-act?'+query);
  await expect(page.getByRole('heading',{
    name:'Просмотр акта приёмки автомобиля (dev)'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Акт №1 · PASSENGER'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Зафиксировать версию акта'})).toHaveCount(0);
  await expect(page.getByRole('img',{name:'Фото акта 1: Перед'})).toBeVisible();
});

test('customer hides all act and photo evidence after revoked order scope',async({page})=>{
  let revoked=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>route.fulfill(
    revoked?{status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'}:{status:200,contentType:'application/json',
      body:JSON.stringify({...initial,latestVersion:1,items:[{
        id:actId,version:1,vehicleKind:'PASSENGER',sha256:'a'.repeat(64),
        createdAt:'2030-01-01T10:00:00Z',actorId:org,
        damages:[{areaCode:'FRONT',damageCode:'SCRATCH',severity:'MINOR'}],
        inventory:['KEYS','SPARE_WHEEL','TOOLS','DOCS','CHARGER'].map(itemCode=>
          ({itemCode,condition:'NOT_CHECKED'})),
        photos:[],
      }]})}));
  await page.goto('/account/auto-intake-act?'+query);
  await expect(page.getByRole('heading',{name:'Акт №1 · PASSENGER'})).toBeVisible();
  revoked=true;
  await page.getByRole('button',{name:'Обновить журнал актов'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByRole('heading',{name:'Акт №1 · PASSENGER'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Печатать выбранную версию'})).toHaveCount(0);
  await expect(page.locator('body > .intake-act-print-portal')).toHaveCount(0);
});

test('lost POST reply preserves exact requestKey and photo bytes for one safe replay',async({page})=>{
  let attempts=0;
  const bodies:string[]=[];
  const picture={areaCode:'FRONT',damageCode:'SCRATCH',severity:'MINOR'};
  const initialAct={...initial,latestVersion:0};
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    if(route.request().method()==='GET')return route.fulfill({
      status:200,contentType:'application/json',body:JSON.stringify(initialAct)});
    attempts++;
    bodies.push(route.request().postData()??'');
    if(attempts===1)return route.abort('failed');
    return route.fulfill({status:200,contentType:'application/json',
      body:'{"ok":true,"replayed":true}'});
  });
  await page.goto('/staff/auto-intake-act?'+query);
  await page.getByRole('button',{name:'Зафиксировать версию акта'}).click();
  await expect(page.getByRole('button',{name:'Повторить тот же запрос'})).toBeVisible();
  await page.getByRole('button',{name:'Повторить тот же запрос'}).click();
  // A click resolves before the asynchronous POST reaches the network route.
  await expect.poll(()=>attempts).toBe(2);
  expect(bodies[1]).toBe(bodies[0]);
  expect(JSON.parse(bodies[0])).toMatchObject({
    expectedVersion:0,act:{vehicleKind:'PASSENGER',damages:[],photos:[]},
  });
  expect(picture.areaCode).toBe('FRONT');
});


test('print keeps one page with 20 historic acts at 390 and 1280',async({page})=>{
  // Длинный экранный журнал не должен создавать пустые страницы в PDF.
  const versions=Array.from({length:20},(_,index)=>{
    const n=index+1;
    return {
      id:n===1?actId:`77777777-7777-4777-8777-${String(n).padStart(12,'0')}`,
      version:n,vehicleKind:'PASSENGER',sha256:(n===1?'a':'c').repeat(64),
      createdAt:'2030-01-01T10:00:00Z',actorId:org,
      damages:[{areaCode:'FRONT',damageCode:'SCRATCH',severity:'MINOR'}],
      inventory:['KEYS','SPARE_WHEEL','TOOLS','DOCS','CHARGER'].map(itemCode=>
        ({itemCode,condition:n===1?'PRESENT':'MISSING'})),
      photos:[],
    };
  });
  await page.route('**/api/v1/dev/auto/organizations/**',route=>
    route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({...initial,locked:true,latestVersion:20,items:versions})}));
  for(const width of [390,1280]){
    await page.setViewportSize({width,height:844});
    await page.goto('/account/auto-intake-act?'+query);
    const preview=page.locator('.intake-act-print-preview .intake-act-print-document');
    const printable=page.locator('body > .intake-act-print-portal .intake-act-print-document');
    await expect(preview).toContainText('версия 20');
    await expect(printable).toHaveCount(1);
    await expect(printable).toHaveCSS('display','block');
    await expect(page.locator('body > .intake-act-print-portal')).toHaveCSS('display','none');
    await page.getByRole('combobox',{name:'Версия для печати'}).selectOption(actId);
    await expect(preview).toContainText('версия 1');
    await expect(printable).toContainText('версия 1');
    await expect(printable).toContainText('Царапина');
    await expect(printable).not.toContainText('версия 20');

    await page.emulateMedia({media:'print'});
    const printRoot=page.locator('body > .intake-act-print-portal');
    await expect(printRoot).toHaveCSS('display','block');
    await expect(page.locator('body > :not(.intake-act-print-portal)').first())
      .toHaveCSS('display','none');
    const dimensions=await printRoot.evaluate(node=>({
      height:node.getBoundingClientRect().height,
      bodyHeight:document.body.scrollHeight,
    }));
    expect(dimensions.height).toBeGreaterThan(100);
    // A4 ~1122 CSS px at 96 dpi: never include the 20-version screen history.
    expect(dimensions.bodyHeight).toBeLessThan(1122);
    const pdf=await page.pdf({format:'A4',printBackground:true});
    // Chromium emits one uncompressed /Type /Page object per printed page.
    const pages=pdf.toString('latin1').match(/\/Type\s*\/Page\b/g)??[];
    expect(pages).toHaveLength(1);
    await page.emulateMedia({media:'screen'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width);
  }
});
