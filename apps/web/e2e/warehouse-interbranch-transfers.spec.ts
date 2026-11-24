import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const src='22222222-2222-4222-8222-222222222222';
const dst='33333333-3333-4333-8333-333333333333';
const la='44444444-4444-4444-8444-444444444444';
const lb='55555555-5555-4555-8555-555555555555';
const ident='66666666-6666-4666-8666-666666666666';

test('warehouse confirms interbranch transfer, exact retry, refreshed ledger and denial',async({page})=>{
  let sent=false,denied=false,lost=true;
  const requests:{key:string;body:string}[]=[];
  const lot={article:'SYN-TO',batchCode:'BATCH',binCode:'BIN'};
  const snapshot=()=>({syntheticOnly:true,mode:'INTERBRANCH',
    source:{truncated:false,nextCursor:null,items:[{...lot,lotId:la,allocated:sent?'2':'5',available:sent?'0':'3'}]},
    destination:{truncated:false,nextCursor:null,items:[{...lot,lotId:lb,allocated:sent?'3':'0',available:sent?'3':'0'}]},
    items:sent?[{transferId:ident,createdAt:'2030-01-01T10:00:00Z',sourceLotId:la,destinationLotId:lb,
      article:lot.article,quantity:'3',actorId:org,debitStockId:src,creditStockId:dst}]:[],nextCursor:null});
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.route('**/warehouse/interbranch-transfers**',route=>{
    const req=route.request();
    if(denied)return route.fulfill({status:403,contentType:'application/json',body:'{}'});
    if(req.method()==='GET'){
      const q=new URL(req.url()).searchParams;
      expect(q.get('sourceLocationId')).toBe(src);expect(q.get('destinationLocationId')).toBe(dst);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(snapshot())});
    }
    expect(req.headers()['x-csrf-token']).toBe('csrf');
    expect(req.postDataJSON()).toEqual({sourceLocationId:src,destinationLocationId:dst,sourceLotId:la,destinationLotId:lb,quantity:3});
    requests.push({key:req.headers()['idempotency-key'],body:req.postData()??''});
    sent=true;
    if(lost){lost=false;return route.abort('failed');}
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({syntheticOnly:true,transferId:ident,replayed:true})});
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/warehouse-interbranch-transfers');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала отправления').fill(src);
  await page.getByLabel('UUID филиала назначения').fill(dst);
  await page.getByLabel('Поиск артикула').fill('SYN-TO');
  await page.getByRole('button',{name:'Загрузить партии и журнал'}).click();
  await page.getByLabel('Исходная партия').selectOption(la);
  await page.getByLabel('Целевая партия того же артикула').selectOption(lb);
  await page.getByLabel('Количество').fill('4');
  await expect(page.getByRole('button',{name:'Подготовить перемещение'})).toBeDisabled();
  await page.getByLabel('Количество').fill('3');
  await page.getByRole('button',{name:'Подготовить перемещение'}).click();
  await page.getByRole('button',{name:'Подтвердить перемещение'}).click();
  await expect(page.getByText('Результат неизвестен.',{exact:false})).toBeVisible();
  await expect(page.getByLabel('UUID филиала назначения')).toBeDisabled();
  await page.getByRole('button',{name:'Повторить исходную операцию'}).click();
  await expect(page.getByText('Перемещение подтверждено:',{exact:false})).toBeVisible();
  expect(requests).toHaveLength(2);expect(requests[0]).toEqual(requests[1]);
  await page.getByRole('button',{name:'Загрузить партии и журнал'}).click();
  await expect(page.getByText('SYN-TO · 3 ед.',{exact:true})).toBeVisible();
  await page.getByText('Связанные проводки',{exact:true}).click();
  await expect(page.getByText('Расход '+src+' · приход '+dst)).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Загрузить партии и журнал'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('SYN-TO · 3 ед.',{exact:true})).toHaveCount(0);
  await expect(page.getByLabel('Исходная партия')).toHaveCount(0);
});


test('warehouse selects late lots independently and moves within branch without physical stock events',async({page})=>{
  const late='77777777-7777-4777-8777-777777777777';
  const other='88888888-8888-4888-8888-888888888888';
  const make=(i:number)=>({lotId:'aaaaaaaa-aaaa-4aaa-8aaa-'+String(i).padStart(12,'0'),
    article:'SYN-TO',batchCode:'BATCH-'+i,binCode:'BIN-A',allocated:'0',available:'0'});
  const filler=Array.from({length:50},(_,i)=>make(i+1));
  const source={...make(51),lotId:late,allocated:'2',available:'2'};
  const destination={...make(52),lotId:other,binCode:'BIN-B'};
  let posted=false;
  await page.route('**/api/v1/auth/csrf',r=>r.fulfill({status:200,contentType:'application/json',body:'{"csrfToken":"csrf"}'}));
  await page.route('**/warehouse/interbranch-transfers**',route=>{
    const req=route.request(),q=new URL(req.url()).searchParams;
    if(req.method()==='POST'){
      expect(req.postDataJSON()).toEqual({mode:'LOCAL',sourceLocationId:src,destinationLocationId:src,
        sourceLotId:late,destinationLotId:other,quantity:1});
      posted=true;
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({syntheticOnly:true,transferId:ident,replayed:false})});
    }
    expect(q.get('mode')).toBe('LOCAL');expect(q.get('destinationLocationId')).toBe(src);
    const side=(key:string,lot:typeof source)=>q.has(key)?
      {items:[lot],truncated:false,nextCursor:null}:
      {items:filler,truncated:true,nextCursor:key+'-tail'};
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
      syntheticOnly:true,mode:'LOCAL',source:side('sourceCursor',source),
      destination:side('destinationCursor',destination),nextCursor:null,
      items:posted?[{transferId:ident,createdAt:'2030-01-01T10:00:00Z',
        sourceLotId:late,destinationLotId:other,article:'SYN-TO',quantity:'1',
        actorId:org,debitAllocationId:src,creditAllocationId:dst}]:[]})});
  });
  await page.goto('/staff/warehouse-interbranch-transfers');
  await page.getByLabel('Вид перемещения').selectOption('LOCAL');
  await page.getByLabel('UUID организации').fill(org);
  await page.getByLabel('UUID филиала отправления').fill(src);
  await page.getByRole('button',{name:'Загрузить партии и журнал'}).click();
  await page.getByRole('button',{name:'Следующие исходные партии'}).click();
  await page.getByLabel('Исходная партия').selectOption(late);
  await page.getByRole('button',{name:'Следующие целевые партии'}).click();
  await expect(page.getByLabel('Исходная партия')).toHaveValue(late);
  await page.getByLabel('Целевая партия того же артикула').selectOption(other);
  await page.getByRole('button',{name:'Подготовить перемещение'}).click();
  await page.getByRole('button',{name:'Подтвердить перемещение'}).click();
  await expect(page.getByText('Перемещение подтверждено:',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Загрузить партии и журнал'}).click();
  await page.getByText('Связанные проводки',{exact:true}).click();
  await expect(page.getByText('Снятие размещения '+src+' · размещение '+dst)).toBeVisible();
});
