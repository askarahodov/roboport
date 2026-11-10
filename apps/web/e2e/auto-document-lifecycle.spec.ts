import {test,expect} from '@playwright/test';

const org='11111111-1111-4111-8111-111111111111';
const loc='22222222-2222-4222-8222-222222222222';
const order='33333333-3333-4333-8333-333333333333';
const previewId='44444444-4444-4444-8444-444444444444';
const actId='55555555-5555-4555-8555-555555555555';
const previewDigest='a'.repeat(64),actDigest='b'.repeat(64);
const base='/api/v1/dev/auto/organizations/'+org+'/locations/'+loc+'/orders/'+order+'/demo-close';
const snapshot={
  syntheticOnly:true,releaseAllowed:false,legalGatePassed:false,currency:'RUB',
  approvedVersion:1,approvedBaseMinor:2500,approvedExtraMinor:0,
  approvedExtraVersion:null,approvedExtraRevisionId:null,approvedTotalMinor:2500,
  simulatedPaidMinor:2500,simulatedRefundedMinor:0,simulatedDueMinor:0,
  events:[{id:previewId,kind:'SIM_PAYMENT',amountMinor:2500,
    createdAt:'2030-01-01T10:00:00Z'}],
  outstandingReserves:[],unreceivedMaterialCount:0,
  executionStatus:'QC_ACCEPTED',blockers:[],canSealPreview:false,
  preview:{id:previewId,revisionId:previewId,digest:previewDigest,
    createdAt:'2030-01-01T10:01:00Z',snapshot:{}},
};
type Action='POSTED'|'CLIENT_ACK'|'CLIENT_DISPUTE'|'CLOSED_TECHNICAL';

test('390/1280: exact intake ACK -> posting -> client review -> technical close without release',async({page},testInfo)=>{
  let actions:Action[]=[];
  const payloads:any[]=[];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    const request=route.request(),pathname=new URL(request.url()).pathname;
    if(pathname===base&&request.method()==='GET'){
      const status=actions.includes('CLOSED_TECHNICAL')?'CLOSED_TECHNICAL':
        actions.includes('CLIENT_DISPUTE')?'CLIENT_DISPUTED':
        actions.includes('CLIENT_ACK')?'CLIENT_ACKNOWLEDGED':
        actions.includes('POSTED')?'POSTED':'DRAFT';
      return route.fulfill({status:200,contentType:'application/json',
        body:JSON.stringify({...snapshot,documentWorkflow:{
          syntheticOnly:true,releaseAllowed:false,legalSignatureStatus:'NOT_IMPLEMENTED',
          status,intakeAct:{id:actId,version:1,digest:actDigest,clientDecision:'ACKNOWLEDGED'},
          events:actions.map((action,i)=>({
            id:previewId,action,actorId:org,previewId,previewDigest,
            intakeActId:actId,intakeDigest:actDigest,
            createdAt:new Date(Date.UTC(2030,0,1,11+i)).toISOString(),
          })),canReview:status==='POSTED',canClose:status==='CLIENT_ACKNOWLEDGED',
        }})});
    }
    if(pathname===base+'/document-events'&&request.method()==='POST'){
      expect(request.headers()['x-csrf-token']).toBe('synthetic');
      const body=request.postDataJSON();
      expect(body.requestKey).toMatch(/^[0-9a-f-]{36}$/);
      expect(body).toMatchObject({
        previewId,previewDigest,intakeActId:actId,intakeDigest:actDigest,
      });
      actions.push(body.action);payloads.push(body);
      return route.fulfill({status:201,contentType:'application/json',
        body:JSON.stringify({ok:true,replayed:false})});
    }
    throw Error('Unexpected '+pathname);
  });
  await page.setViewportSize({width:390,height:844});
  await page.goto('/staff/auto-demo-close');
  for(const [label,value] of [
    ['UUID организации',org],['UUID филиала',loc],['UUID заказ-наряда',order],
  ] as const)await page.getByRole('textbox',{name:label}).fill(value);
  await expect(page.getByText('Статус документа: DRAFT.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Технически провести документ (MANAGER)'}).click();
  await expect(page.getByText('Статус документа: POSTED.',{exact:false})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('posted-document-390.png'),
    fullPage:true,animations:'disabled'});
  await page.goto('/account/auto-demo-close');
  for(const [label,value] of [
    ['UUID организации',org],['UUID филиала',loc],['UUID заказ-наряда',order],
  ] as const)await page.getByRole('textbox',{name:label}).fill(value);
  await expect(page.getByRole('button',{name:'Подтвердить просмотр пакета (не подпись)'})).toBeVisible();
  await page.getByRole('button',{name:'Подтвердить просмотр пакета (не подпись)'}).click();
  await expect(page.getByText('Статус документа: CLIENT_ACKNOWLEDGED.',{exact:false})).toBeVisible();
  await page.setViewportSize({width:1280,height:900});
  await page.goto('/staff/auto-demo-close');
  for(const [label,value] of [
    ['UUID организации',org],['UUID филиала',loc],['UUID заказ-наряда',order],
  ] as const)await page.getByRole('textbox',{name:label}).fill(value);
  await page.getByRole('button',{name:'Технически закрыть журнал (НЕ выдавать автомобиль)'}).click();
  await expect(page.getByText('Статус документа: CLOSED_TECHNICAL.',{exact:false})).toBeVisible();
  await expect(page.getByRole('note')).toContainText('Разрешение на выдачу: НЕТ');
  await expect(page.getByRole('button',{name:'Технически закрыть журнал (НЕ выдавать автомобиль)'})).toHaveCount(0);
  expect(actions).toEqual(['POSTED','CLIENT_ACK','CLOSED_TECHNICAL']);
  expect(payloads).toHaveLength(3);
  await page.screenshot({path:testInfo.outputPath('technically-closed-1280.png'),
    fullPage:true,animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
});

test('disputed document cannot show closure; revoked rights erase the workflow',async({page})=>{
  let denied=false;
  const actions:Action[]=['POSTED'];
  await page.route('**/api/v1/auth/csrf',route=>route.fulfill({
    status:200,contentType:'application/json',body:'{"csrfToken":"synthetic"}',
  }));
  await page.route('**/api/v1/dev/auto/organizations/**',route=>{
    if(denied)return route.fulfill({status:403,contentType:'application/json',
      body:'{"code":"FORBIDDEN"}'});
    if(route.request().method()==='POST'){
      actions.push(route.request().postDataJSON().action);
      return route.fulfill({status:201,contentType:'application/json',
        body:'{"ok":true}'});
    }
    const status=actions.includes('CLIENT_DISPUTE')?'CLIENT_DISPUTED':'POSTED';
    return route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify({...snapshot,documentWorkflow:{
        syntheticOnly:true,releaseAllowed:false,legalSignatureStatus:'NOT_IMPLEMENTED',
        status,intakeAct:{id:actId,version:1,digest:actDigest,clientDecision:'ACKNOWLEDGED'},
        events:[],canReview:status==='POSTED',canClose:false,
      }})});
  });
  await page.goto('/account/auto-demo-close');
  await page.getByRole('textbox',{name:'UUID организации'}).fill(org);
  await page.getByRole('textbox',{name:'UUID филиала'}).fill(loc);
  await page.getByRole('textbox',{name:'UUID заказ-наряда'}).fill(order);
  await page.getByRole('button',{name:'Оспорить технический пакет'}).click();
  await expect(page.getByText('Статус документа: CLIENT_DISPUTED.',{exact:false})).toBeVisible();
  await expect(page.getByText('Техническое закрытие запрещено;',{exact:false})).toBeVisible();
  denied=true;
  await page.getByRole('button',{name:'Обновить сверку'}).click();
  await expect(page.getByRole('alert')).toContainText('Доступ запрещён');
  await expect(page.getByText('Статус документа: CLIENT_DISPUTED.',{exact:false})).toHaveCount(0);
});
