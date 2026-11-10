// Real Keycloak Code+PKCE with two synthetic confirmed vehicle owners (no robot URLs).
import { chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';

const origin='https://127.0.0.1:8444';
const vehicles=origin+'/api/v1/dev/auto/vehicles';
const browser=await chromium.launch({headless:true});
function revoke(sql){
  const program=[
    'import psycopg,os',
    "url=os.environ['ROBOport_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)",
    'with psycopg.connect(url) as db:',
    ' db.execute('+JSON.stringify(sql)+')',
  ].join('\n');
  execFileSync('python',['-c',program],{stdio:'inherit'});
}
async function login(name,password,expectedUser,ownVehicle,foreignVehicle){
  const context=await browser.newContext({ignoreHTTPSErrors:true});
  const page=await context.newPage();
  try{
    await page.goto(origin+'/api/v1/dev/oidc/start',{waitUntil:'domcontentloaded'});
    await page.locator('#username').fill(name);
    await page.locator('#password').fill(password);
    await page.locator('#kc-login').click();
    try {await page.waitForURL(origin+'/account',{timeout:15000});}
    catch(error){
      console.error('OIDC navigation URL:',page.url());
      console.error('OIDC messages:',(await page.locator('h1,h2,[role="alert"],.alert-error,.kc-feedback-text').allTextContents()).join(' | ').slice(0,800));
      throw error;
    }
    const who=await context.request.get(origin+'/api/v1/auth/me');
    if(who.status()!==200)throw Error(name+' session status '+who.status()+' '+await who.text());
    if((await who.json()).subjectId!==expectedUser)throw Error(name+' incorrect OIDC user binding');
    const result=await context.request.get(vehicles);
    if(result.status()!==200)throw Error(name+' vehicle list status '+result.status()+' '+await result.text());
    if(result.headers()['cache-control']!=='no-store')throw Error('private vehicle list must be noncacheable');
    const items=(await result.json()).items;
    if(items.length!==1||items[0].id!==ownVehicle||items.some(x=>x.id===foreignVehicle))
      throw Error(name+' automotive BOLA leak '+JSON.stringify(items));
    if('vin' in items[0]||items[0].vinLast4===undefined)throw Error('unmasked vehicle VIN');
    const own=await context.request.get(vehicles+'/'+ownVehicle);
    if(own.status()!==200)throw Error('own car is inaccessible: '+own.status());
    const foreign=await context.request.get(vehicles+'/'+foreignVehicle);
    if(foreign.status()!==404)throw Error('foreign vehicle IDOR: '+foreign.status());
    const retired=await context.request.get(origin+'/api/v1/catalog/brands');
    if(retired.status()!==404)throw Error('retired robot catalog was mounted');
    return context;
  }catch(error){await context.close();throw error;}
}
try{
 const a=await login('synthetic-a','synthetic-password-a','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'dddddddd-dddd-4ddd-8ddd-dddddddddddd','ffffffff-ffff-4fff-8fff-ffffffffffff');
 const b=await login('synthetic-b','synthetic-password-b','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
   'ffffffff-ffff-4fff-8fff-ffffffffffff','dddddddd-dddd-4ddd-8ddd-dddddddddddd');
 revoke("UPDATE account_customer_link SET revoked_at=now() WHERE user_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'");
 const after=await b.request.get(vehicles);
 if(after.status()!==200||(await after.json()).items.length!==0)
   throw Error('revoked link still exposes vehicles');
 const afterDetail=await b.request.get(vehicles+'/ffffffff-ffff-4fff-8fff-ffffffffffff');
 if(afterDetail.status()!==404)throw Error('revoked link still exposes individual vehicle');
 const unaffected=await a.request.get(vehicles);
 if(unaffected.status()!==200||(await unaffected.json()).items.length!==1)
   throw Error('revoking customer B affected customer A');
 revoke("UPDATE user_session SET revoked_at=now() WHERE user_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'");
 const denied=await b.request.get(origin+'/api/v1/auth/me');
 if(denied.status()!==401)throw Error('revoked Keycloak-linked session still valid');
 const deniedVehicles=await b.request.get(vehicles);
 if(deniedVehicles.status()!==401)throw Error('revoked session still reads automotive data');
 await a.close();await b.close();
 console.log('LIVE KEYCLOAK PKCE + AUTOMOTIVE A/B BOLA + REVOKE: PASS');
}finally{await browser.close();}
