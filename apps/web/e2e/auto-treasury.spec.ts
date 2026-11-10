import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const account='44444444-4444-4444-8444-444444444444';
const revision='55555555-5555-4555-8555-555555555555';
const base='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/treasury';
const day=new Date().toISOString().slice(0,10);
type Movement={kind:string;amountMinor:number;requestKey:string;accountId:string};
function moneyState(items:Movement[]){
  const sum=(k:string)=>items.filter(e=>e.kind===k).reduce((s,e)=>s+e.amountMinor,0);
  const advanced=sum('ADVANCE'),payment=sum('PAYMENT'),applied=sum('APPLY_ADVANCE'),
    ra=sum('REFUND_ADVANCE'),rp=sum('REFUND_PAYMENT');
  return {advancedMinor:advanced,paymentMinor:payment,appliedMinor:applied,
    refundedAdvanceMinor:ra,refundedPaymentMinor:rp,
    unappliedAdvanceMinor:advanced-applied-ra,
    settledMinor:payment+applied-rp,cashNetMinor:advanced+payment-ra-rp,
    dueMinor:2500-payment-applied+rp};
}

test('full cashier workstation, exact lost POST retry and day close 390/1280 dark',async({page},info)=>{
  let accounts=false,closed=false,firstLost=true;
  const attempts:Movement[]=[];
  const actions:string[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname,verb=req.method();
    if(path===base+'/accounts'&&verb==='GET'){
      const sum=moneyState(attempts).cashNetMinor;
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,realMoney:false,currency:'RUB',items:accounts?[{
          id:account,code:'CASH-01',label:'Тестовая касса',
          kind:'CASH_SIM',currency:'RUB',balanceMinor:sum,
          lastClosedDay:closed?day:null,
        }]:[],
      })});
    }
    if(path===base+'/orders/'+order&&verb==='GET'){
      const values=moneyState(attempts);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,realMoney:false,releaseAllowed:false,orderId:order,
        currency:'RUB',approvedRevisionId:revision,approvedTotalMinor:2500,
        ...values,events:attempts.map((x,i)=>({
          id:revision,accountId:account,revisionId:revision,kind:x.kind,
          amountMinor:x.amountMinor,reason:'Тестовая операция',
          businessDay:day,createdAt:new Date(Date.UTC(2030,0,1,12+i)).toISOString(),
        })),
      })});
    }
    if(path===base+'/report'&&verb==='GET'){
      const incoming=attempts.filter(x=>['ADVANCE','PAYMENT'].includes(x.kind))
        .reduce((s,x)=>s+x.amountMinor,0);
      const outgoing=attempts.filter(x=>x.kind.startsWith('REFUND_'))
        .reduce((s,x)=>s+x.amountMinor,0);
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,realMoney:false,currency:'RUB',
        basis:'INDEPENDENT_TREASURY_EVENT_DAYS',accounts:[],items:accounts?[{
          accountId:account,accountCode:'CASH-01',kind:'CASH_SIM',localDay:day,
          openingMinor:0,incomingMinor:incoming,outgoingMinor:outgoing,
          closingMinor:incoming-outgoing,closeId:closed?revision:null,
          closedAt:closed?'2030-01-01T13:00:00Z':null,
        }]:[],
      })});
    }
    if(verb==='POST'&&path.startsWith(base)){
      expect(req.headers()['x-csrf-token']).toBe('synthetic');
      const body=req.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      actions.push(path);
      if(path===base+'/accounts')accounts=true;
      else if(path===base+'/accounts/'+account+'/close-day')closed=true;
      else if(path===base+'/orders/'+order+'/events'){
        if(!attempts.some(x=>x.requestKey===body.requestKey))
          attempts.push(body as Movement);
        if(firstLost){
          firstLost=false;
          return route.abort('failed');
        }
      }else throw Error('Unexpected mutation '+path);
      return route.fulfill({status:actions.length===2?200:201,
        contentType:'application/json',body:JSON.stringify({
          syntheticOnly:true,replayed:false,
        })});
    }
    throw Error('Unexpected URL '+verb+' '+path);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-treasury');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByRole('heading',{name:'Создать тестовую кассу / счёт (MANAGER)'})).toBeVisible();
  await page.getByRole('button',{name:'Создать тестовый счёт'}).click();
  await expect(page.getByRole('listitem').getByText('CASH-01 · Тестовая касса',{exact:false})).toBeVisible();
  await page.getByRole('combobox',{name:'Счёт для операций'}).selectOption(account);
  await page.getByRole('spinbutton',{name:'Сумма, коп.'}).fill('1200');
  await page.getByRole('button',{name:'Записать движение (не реальные деньги)'}).click();
  await expect(page.getByRole('group',{name:'Повтор неизвестного запроса'})).toBeVisible();
  await page.getByRole('button',{name:'Повторить тот же запрос'}).click();
  await expect(page.getByText('Незачтённый аванс: 12,00.',{exact:false})).toBeVisible();
  expect(attempts.length).toBe(1);
  await page.screenshot({path:info.outputPath('treasury-390-light.png'),
    fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'Сверить модельные остатки'}).click();
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('button',{name:'Зафиксировать закрытие дня'}).click();
  await expect(page.getByText('последний закрытый день:',{exact:false})).toContainText(day);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('combobox',{name:'Тема оформления'}).selectOption('dark');
  await page.getByRole('button',{name:'Сверить модельные остатки'}).click();
  await expect(page.getByRole('cell',{name:'Зафиксирован'})).toBeVisible();
  await page.screenshot({path:info.outputPath('treasury-1280-dark-closed.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  expect(actions.filter(x=>x===base+'/orders/'+order+'/events').length).toBe(2);
});

test('revocation and cross-scope 403 clear prices and events',async({page})=>{
  let denied=false;
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(denied)return route.fulfill({status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'});
    if(path===base+'/accounts')return route.fulfill({status:200,
      contentType:'application/json',body:JSON.stringify({
        syntheticOnly:true,realMoney:false,currency:'RUB',items:[{
          id:account,code:'CASH-01',label:'Тестовая касса',kind:'CASH_SIM',
          currency:'RUB',balanceMinor:1200,lastClosedDay:null,
        }],
      })});
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({syntheticOnly:true,realMoney:false,releaseAllowed:false,
        orderId:order,currency:'RUB',approvedRevisionId:revision,
        approvedTotalMinor:2500,advancedMinor:1200,paymentMinor:0,
        appliedMinor:0,refundedAdvanceMinor:0,refundedPaymentMinor:0,
        unappliedAdvanceMinor:1200,settledMinor:0,cashNetMinor:1200,
        dueMinor:2500,events:[]})});
  });
  await page.goto('/staff/auto-treasury');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await expect(page.getByText('Незачтённый аванс:',{exact:false})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить журнал'}).click();
  await expect(page.getByRole('alert')).toContainText('доступ запрещён');
  await expect(page.getByText('Незачтённый аванс:',{exact:false})).toHaveCount(0);
  await expect(page.getByRole('listitem').getByText('CASH-01 · Тестовая касса',{exact:false})).toHaveCount(0);
});
